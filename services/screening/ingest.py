"""Loads a sanctions list into a new, versioned Qdrant collection and points the screening alias
at it. Run by hand (or on a schedule) as an operator task:

    uv run python -m services.screening.ingest --source ofac             # download and activate
    uv run python -m services.screening.ingest --source ofac --from-dir DIR
    uv run python -m services.screening.ingest --source sample           # the 10 synthetic names
    uv run python -m services.screening.ingest --list                    # snapshots on record
    uv run python -m services.screening.ingest --activate COLLECTION     # roll back or forward

Connects as sentrilog_app (DATABASE_URL), which may insert list_snapshots rows but never update
or delete them (007_list_snapshots_and_eval_runs.sql).
"""

import argparse
import asyncio
import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from . import ofac
from .config import settings
from .data import SAMPLE_SANCTIONS_LIST
from .embeddings import EmbeddingClient, build_embedding_client, embedding_model_version
from .qdrant_store import resolve_alias
from .snapshots import (
    Snapshot,
    SourceEntry,
    activate,
    build_collection,
    collection_for,
    make_list_version,
    record_snapshot,
)


def ingest_entries(
    client: EmbeddingClient,
    entries: Sequence[SourceEntry],
    *,
    source: str,
    content_hash: str,
    embedding_model: str,
    fetched_at: datetime,
) -> Snapshot:
    list_version = make_list_version(source, content_hash, fetched_at)
    collection_name = collection_for(list_version, embedding_model)
    written = build_collection(
        client, entries, source=source, list_version=list_version, collection_name=collection_name
    )
    return Snapshot(
        collection_name=collection_name,
        list_version=list_version,
        source=source,
        content_hash=content_hash,
        entry_count=written,
        embedding_model=embedding_model,
        fetched_at=fetched_at,
    )


def ingest_sample_list(client: EmbeddingClient, *, embedding_model: str = "test") -> Snapshot:
    """The synthetic 10-name list from data.py, as a snapshot like any other."""
    entries = [SourceEntry(entity_id=None, name=e.name) for e in SAMPLE_SANCTIONS_LIST]
    content = "\n".join(e.name for e in SAMPLE_SANCTIONS_LIST).encode("utf-8")
    return ingest_entries(
        client,
        entries,
        source="SAMPLE",
        content_hash=hashlib.sha256(content).hexdigest(),
        embedding_model=embedding_model,
        fetched_at=datetime.now(UTC),
    )


def ofac_entries(sdn_csv: bytes, alt_csv: bytes) -> list[SourceEntry]:
    return [SourceEntry(e.entity_id, e.name) for e in ofac.parse_sdn(sdn_csv, alt_csv)]


async def _record(snapshot: Snapshot) -> None:
    conn = await asyncpg.connect(settings.database_url.get_secret_value())
    try:
        await record_snapshot(conn, snapshot)
    finally:
        await conn.close()


async def _list() -> None:
    conn = await asyncpg.connect(settings.database_url.get_secret_value())
    try:
        rows = await conn.fetch(
            "SELECT collection_name, list_version, entry_count, embedding_model, created_at "
            "FROM list_snapshots ORDER BY created_at"
        )
    finally:
        await conn.close()
    active = resolve_alias(settings.qdrant_collection)
    for r in rows:
        marker = "*" if r["collection_name"] == active else " "
        print(
            f"{marker} {r['collection_name']}  {r['entry_count']:>6} entries  "
            f"{r['embedding_model']}  {r['created_at']:%Y-%m-%d %H:%M}"
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m services.screening.ingest")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", choices=["ofac", "sample"])
    group.add_argument("--list", action="store_true")
    group.add_argument("--activate", metavar="COLLECTION")
    parser.add_argument("--from-dir", type=Path, help="read SDN.CSV/ALT.CSV (or .gz) from here")
    parser.add_argument("--no-activate", action="store_true", help="build and record only")
    args = parser.parse_args(argv)

    if args.list:
        asyncio.run(_list())
        return
    if args.activate:
        activate(args.activate)
        print(f"'{settings.qdrant_collection}' -> {args.activate}")
        return

    client = build_embedding_client(settings)
    model = embedding_model_version(settings)
    if args.source == "sample":
        snapshot = ingest_sample_list(client, embedding_model=model)
    else:
        if args.from_dir:
            sdn, alt = ofac.read_from_dir(args.from_dir)
        else:
            sdn, alt = ofac.download()
        snapshot = ingest_entries(
            client,
            ofac_entries(sdn, alt),
            source=ofac.LIST_SOURCE,
            content_hash=ofac.content_hash(sdn, alt),
            embedding_model=model,
            fetched_at=datetime.now(UTC),
        )
    asyncio.run(_record(snapshot))
    print(f"built {snapshot.collection_name}: {snapshot.entry_count} entries")
    if not args.no_activate:
        activate(snapshot.collection_name)
        print(f"'{settings.qdrant_collection}' -> {snapshot.collection_name}")


if __name__ == "__main__":
    main()
