"""Qdrant collection for sanctions entries: one point per (list_source, name) with an
embedding of the normalised name as its vector and the full entry as payload.

Every function takes an optional collection_name, defaulting to settings.qdrant_collection --
lets tests point at an isolated, disposable collection instead of the shared dev one seeded by
ingest.py. Since Phase 13 that default is an alias over one collection per list snapshot; Qdrant
resolves an alias anywhere a collection name is accepted, so the read paths here are unchanged.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from .config import settings

_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.qdrant_url)
    return _client


def ensure_collection(collection_name: str | None = None, *, size: int | None = None) -> None:
    """size defaults to EMBEDDING_DIMENSIONS; snapshot builds pass the size of the vectors they
    actually produced, so a model/setting mismatch fails here rather than at the first upsert."""
    collection_name = collection_name or settings.qdrant_collection
    client = get_qdrant_client()
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=size or settings.embedding_dimensions, distance=models.Distance.COSINE
            ),
        )


def drop_collection(collection_name: str) -> None:
    client = get_qdrant_client()
    client.delete_collection(collection_name)


@dataclass(frozen=True)
class EntryPoint:
    list_source: str
    name: str
    vector: list[float]
    phonetic_codes: list[str]
    entity_id: str | None = None
    list_version: str | None = None


def _point(entry: EntryPoint) -> models.PointStruct:
    # Deterministic UUID from (list_source, entity, name): re-ingesting the same source list is
    # an upsert, not a grower of duplicate points. entity_id is part of it because two listed
    # people can share a name, and collapsing them would lose one of the two identities.
    point_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{entry.list_source}:{entry.entity_id or ''}:{entry.name}")
    )
    return models.PointStruct(
        id=point_id,
        vector=entry.vector,
        payload={
            "list_source": entry.list_source,
            "name": entry.name,
            "entity_id": entry.entity_id,
            # Carried on every point so a hit says which snapshot produced it without a second
            # lookup -- the audit question is "which list, on which date" (P-4).
            "list_version": entry.list_version,
            # Stored as payload (not scanned in-memory) so phonetic lookup stays a real Qdrant
            # query and doesn't require pulling the whole list into the screening process.
            "phonetic_codes": entry.phonetic_codes,
        },
    )


def upsert_entry(
    *,
    list_source: str,
    name: str,
    vector: list[float],
    phonetic_codes: list[str],
    entity_id: str | None = None,
    list_version: str | None = None,
    collection_name: str | None = None,
) -> None:
    upsert_entries(
        [EntryPoint(list_source, name, vector, phonetic_codes, entity_id, list_version)],
        collection_name=collection_name,
    )


def upsert_entries(
    entries: Sequence[EntryPoint], *, collection_name: str | None = None, batch_size: int = 512
) -> None:
    collection_name = collection_name or settings.qdrant_collection
    client = get_qdrant_client()
    for start in range(0, len(entries), batch_size):
        client.upsert(
            collection_name=collection_name,
            points=[_point(e) for e in entries[start : start + batch_size]],
        )


def search_by_vector(
    vector: list[float],
    *,
    limit: int | None = None,
    score_threshold: float | None = None,
    collection_name: str | None = None,
) -> list[models.ScoredPoint]:
    collection_name = collection_name or settings.qdrant_collection
    client = get_qdrant_client()
    response = client.query_points(
        collection_name=collection_name,
        query=vector,
        limit=limit or settings.vector_candidates,
        score_threshold=(
            settings.sanctions_vector_threshold if score_threshold is None else score_threshold
        ),
        # Exact, not HNSW: an approximate index can miss a true neighbour, and differently on
        # each build, which would make a compliance decision (and every measured recall)
        # depend on index randomness. At ~16,000 vectors a full scan costs a few milliseconds.
        search_params=models.SearchParams(exact=settings.vector_search_exact),
        with_payload=True,
    )
    return response.points


def search_by_phonetic_codes(
    codes: list[str], *, limit: int = 10, collection_name: str | None = None
) -> list[models.Record]:
    if not codes:
        return []
    collection_name = collection_name or settings.qdrant_collection
    client = get_qdrant_client()
    records, _next_offset = client.scroll(
        collection_name=collection_name,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(key="phonetic_codes", match=models.MatchAny(any=codes))]
        ),
        limit=limit,
        with_payload=True,
    )
    return records


def count_points(collection_name: str) -> int:
    return get_qdrant_client().count(collection_name=collection_name, exact=True).count


def resolve_alias(alias: str) -> str | None:
    """The collection an alias points at, or None if no such alias exists."""
    for a in get_qdrant_client().get_aliases().aliases:
        if a.alias_name == alias:
            return a.collection_name
    return None


def point_alias(alias: str, collection_name: str) -> None:
    """Atomically repoints alias at collection_name: delete and create in one request, so no
    screen ever sees the alias missing."""
    client = get_qdrant_client()
    actions: list[
        models.CreateAliasOperation | models.DeleteAliasOperation | models.RenameAliasOperation
    ] = []
    if resolve_alias(alias) is not None:
        actions.append(
            models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias))
        )
    actions.append(
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=collection_name, alias_name=alias)
        )
    )
    client.update_collection_aliases(change_aliases_operations=actions)
