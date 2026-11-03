"""Versioned list snapshots (improvements.md P-4). Each ingest builds a new Qdrant collection and
records a list_snapshots row; screening reads through an alias pointed at one of them.

Building and activating are separate steps on purpose. A snapshot is checked (its point count
must equal its entry count) before anything reads it, and rolling back is activate() on the
previous collection -- nothing is ever deleted, because sanctions_hits and audit rows name the
list_version a past case was screened against, and that has to stay answerable.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import asyncpg
from qdrant_client import models

from .config import settings
from .embeddings import EmbeddingClient, embed_many
from .names import normalize_name
from .phonetic import phonetic_codes
from .qdrant_store import (
    EntryPoint,
    count_points,
    ensure_collection,
    get_qdrant_client,
    point_alias,
    resolve_alias,
    upsert_entries,
)

_CHUNK = 512


@dataclass(frozen=True)
class SourceEntry:
    entity_id: str | None
    name: str


@dataclass(frozen=True)
class Snapshot:
    collection_name: str
    list_version: str
    source: str
    content_hash: str
    entry_count: int
    embedding_model: str
    fetched_at: datetime
    published_at: datetime | None = None


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def make_list_version(source: str, content_hash: str, fetched_at: datetime) -> str:
    """Readable and unique: the source, the day it was fetched, and enough of the content hash
    that two fetches of different content on one day differ."""
    return f"{slug(source)}-{fetched_at:%Y%m%d}-{content_hash[:10]}"


def collection_for(list_version: str, embedding_model: str) -> str:
    # The model is part of the name: vectors from two models do not share a space, so the same
    # list embedded twice is two collections, never one.
    return f"sanctions-{list_version}-{slug(embedding_model.rsplit('/', 1)[-1])}"


def dedupe(entries: Sequence[SourceEntry]) -> list[tuple[SourceEntry, str]]:
    """(entry, normalised name) pairs, one per (entity, normalised name): OFAC often lists an
    alias that differs from the primary name only in case or punctuation."""
    seen: set[tuple[str | None, str]] = set()
    out: list[tuple[SourceEntry, str]] = []
    for entry in entries:
        normalized = normalize_name(entry.name)
        key = (entry.entity_id, normalized)
        if normalized and key not in seen:
            seen.add(key)
            out.append((entry, normalized))
    return out


def build_collection(
    client: EmbeddingClient,
    entries: Sequence[SourceEntry],
    *,
    source: str,
    list_version: str,
    collection_name: str,
) -> int:
    """Embeds and loads entries into a new collection. Returns the number of points written,
    which the caller checks against the entry count before activating."""
    pairs = dedupe(entries)
    if not pairs:
        raise ValueError(f"{collection_name}: no entries to load")
    ensure_collection(collection_name, size=len(client.embed(pairs[0][1])))
    # A keyword index so the phonetic lookup is an index hit rather than a scan of every point.
    get_qdrant_client().create_payload_index(
        collection_name=collection_name,
        field_name="phonetic_codes",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
    # Embedded and written a chunk at a time, so peak memory is one chunk of vectors rather than
    # the whole list -- 16,000 384-float vectors held as Python lists is a few hundred MB.
    for start in range(0, len(pairs), _CHUNK):
        chunk = pairs[start : start + _CHUNK]
        vectors = embed_many(client, [normalized for _, normalized in chunk])
        points = []
        for (entry, normalized), vector in zip(chunk, vectors, strict=True):
            primary, secondary = phonetic_codes(normalized)
            points.append(
                EntryPoint(
                    list_source=source,
                    # The normalised form is what both the vector and the phonetic codes were
                    # computed from, so it is what a reviewer is shown as the matched name.
                    name=normalized,
                    vector=vector,
                    phonetic_codes=[c for c in (primary, secondary) if c],
                    entity_id=entry.entity_id,
                    list_version=list_version,
                )
            )
        upsert_entries(points, collection_name=collection_name)
    written = count_points(collection_name)
    if written != len(pairs):
        raise RuntimeError(
            f"{collection_name}: wrote {written} points for {len(pairs)} entries -- "
            "not activating a snapshot that does not match its source"
        )
    return written


async def record_snapshot(conn: asyncpg.Connection, snapshot: Snapshot) -> None:
    await conn.execute(
        "INSERT INTO list_snapshots (collection_name, list_version, source, content_hash, "
        "entry_count, embedding_model, published_at, fetched_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT (collection_name) DO NOTHING",
        snapshot.collection_name,
        snapshot.list_version,
        snapshot.source,
        snapshot.content_hash,
        snapshot.entry_count,
        snapshot.embedding_model,
        snapshot.published_at,
        snapshot.fetched_at,
    )


def activate(collection_name: str) -> None:
    point_alias(settings.qdrant_collection, collection_name)


async def active_list_version(conn: asyncpg.Connection) -> str | None:
    """The list_version the screening alias points at, or None if the alias is missing or points
    at a collection that predates snapshots (the pre-Phase 13 sample list)."""
    collection = resolve_alias(settings.qdrant_collection)
    if collection is None:
        return None
    value: str | None = await conn.fetchval(
        "SELECT list_version FROM list_snapshots WHERE collection_name = $1", collection
    )
    return value
