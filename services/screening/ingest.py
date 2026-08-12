"""Loads the sample sanctions list into Qdrant. Run by hand for local dev:
uv run python -m services.screening.ingest
"""

from .config import settings
from .data import SAMPLE_SANCTIONS_LIST
from .embeddings import EmbeddingClient, GeminiEmbeddingClient
from .phonetic import phonetic_codes
from .qdrant_store import ensure_collection, upsert_entry


def ingest_sample_list(client: EmbeddingClient) -> int:
    ensure_collection()
    count = 0
    for entry in SAMPLE_SANCTIONS_LIST:
        vector = client.embed(entry.name)
        primary, secondary = phonetic_codes(entry.name)
        codes = [c for c in (primary, secondary) if c]
        upsert_entry(
            list_source=entry.list_source, name=entry.name, vector=vector, phonetic_codes=codes
        )
        count += 1
    return count


if __name__ == "__main__":
    embedding_client = GeminiEmbeddingClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    n = ingest_sample_list(embedding_client)
    print(f"ingested {n} sample sanctions entries into '{settings.qdrant_collection}'")
