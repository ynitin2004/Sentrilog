from typing import Literal

from pydantic import Field, SecretStr

from services.common.settings_base import ServiceSettings


class Settings(ServiceSettings):
    database_url: SecretStr = Field(json_schema_extra={"secret": True})
    gemini_api_key: SecretStr = Field(json_schema_extra={"secret": True})

    qdrant_url: str = "http://localhost:6333"
    # An alias, not a collection: each list snapshot gets its own collection and ingest points
    # this name at the active one, so rolling back a bad snapshot is moving a pointer
    # (services/screening/snapshots.py). Was the collection "sanctions_entries" before Phase 13.
    qdrant_collection: str = "sanctions_current"

    embedding_provider: Literal["fastembed", "ngram", "gemini"] = "ngram"
    fastembed_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    gemini_embedding_model: str = "gemini-embedding-001"
    # The n-gram vector size the harness measured (D30). Must match the model for "fastembed"
    # (384 for the small models); Gemini accepts any size via output_dimensionality.
    embedding_dimensions: int = 1024
    # Where fastembed keeps downloaded ONNX models. None means fastembed's own temp-dir default.
    model_cache_dir: str | None = None

    # Chosen by the evaluation harness, not by hand (D30, D39): 95.67% recall and 13.45%
    # false-positive rate on screening-queries-v1 against the 2026-11-03 OFAC snapshot. Recorded
    # in services/eval/results/screening.json; tests/eval/test_shipped_config.py fails if these
    # defaults and that file disagree. Was 0.55, for Gemini embeddings, with nothing behind it.
    sanctions_vector_threshold: float = 0.73
    # How many nearest list entries vector search considers before the threshold (and the
    # reranker, if enabled) decides which of them are hits.
    vector_candidates: int = 20
    # Exact nearest-neighbour search (see qdrant_store.search_by_vector). Switch off only if a
    # list grows to where a full scan is too slow, and re-run the harness when you do.
    vector_search_exact: bool = True

    # Double Metaphone lookup alongside vector search (D10). A setting rather than a constant
    # because the harness measures the pair both ways.
    phonetic_enabled: bool = False  # off at the shipped threshold, by measurement (D31)

    reranker_enabled: bool = False
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_threshold: float = 0.0


settings = Settings()  # type: ignore[call-arg]
