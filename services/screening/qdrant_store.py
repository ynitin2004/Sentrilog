"""Qdrant collection for sanctions entries: one point per (list_source, name) with a Gemini
embedding of the name as its vector and the full entry as payload.

Every function takes an optional collection_name, defaulting to settings.qdrant_collection --
lets tests point at an isolated, disposable collection instead of the shared dev one seeded by
ingest.py.
"""

import uuid

from qdrant_client import QdrantClient, models

from .config import settings

_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.qdrant_url)
    return _client


def ensure_collection(collection_name: str | None = None) -> None:
    collection_name = collection_name or settings.qdrant_collection
    client = get_qdrant_client()
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=settings.embedding_dimensions, distance=models.Distance.COSINE
            ),
        )


def drop_collection(collection_name: str) -> None:
    client = get_qdrant_client()
    client.delete_collection(collection_name)


def upsert_entry(
    *,
    list_source: str,
    name: str,
    vector: list[float],
    phonetic_codes: list[str],
    collection_name: str | None = None,
) -> None:
    collection_name = collection_name or settings.qdrant_collection
    client = get_qdrant_client()
    # Deterministic UUID from (list_source, name): re-ingesting the same source list is an
    # upsert, not a grower of duplicate points.
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{list_source}:{name}"))
    client.upsert(
        collection_name=collection_name,
        points=[
            models.PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "list_source": list_source,
                    "name": name,
                    # Stored as payload (not scanned in-memory) so phonetic lookup stays a
                    # real Qdrant query and doesn't require pulling the whole list into the
                    # screening process -- correct at sample-list size and stays correct as
                    # the real list (Phase 9/10) grows to tens of thousands of entries.
                    "phonetic_codes": phonetic_codes,
                },
            )
        ],
    )


def search_by_vector(
    vector: list[float], *, limit: int = 5, collection_name: str | None = None
) -> list[models.ScoredPoint]:
    collection_name = collection_name or settings.qdrant_collection
    client = get_qdrant_client()
    response = client.query_points(
        collection_name=collection_name,
        query=vector,
        limit=limit,
        score_threshold=settings.sanctions_vector_threshold,
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
