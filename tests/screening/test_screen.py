"""screen_name() orchestration tests against a real, isolated Qdrant collection (created and
dropped per test -- never touches the shared dev collection ingest.py seeds) with a fake
embedding client whose vectors have deliberately controlled cosine similarity, so vector-match
and no-match cases are deterministic rather than dependent on a live Gemini call's exact score.

test_real_gemini_and_qdrant_end_to_end below is the one test that uses the real embedding
client, proving the actual integration works -- same "fake for control flow, one real test for
the real integration" split used in Phase 4/5.
"""

import uuid
from collections.abc import Iterator

import pytest

from services.screening.config import settings
from services.screening.phonetic import phonetic_codes
from services.screening.qdrant_store import drop_collection, ensure_collection, upsert_entry
from services.screening.screen import screen_name

_DIM = settings.embedding_dimensions


def _unit_vector(index: int) -> list[float]:
    v = [0.0] * _DIM
    v[index] = 1.0
    return v


class FakeEmbeddingClient:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def embed(self, text: str) -> list[float]:
        return self._vectors[text]


@pytest.fixture
def test_collection() -> Iterator[str]:
    name = f"test-sanctions-{uuid.uuid4()}"
    ensure_collection(name)
    yield name
    drop_collection(name)


def test_vector_match_above_threshold_is_returned(test_collection: str) -> None:
    entry_vector = _unit_vector(0)
    primary, secondary = phonetic_codes("Entry Name")
    upsert_entry(
        list_source="TEST-LIST",
        name="Entry Name",
        vector=entry_vector,
        phonetic_codes=[c for c in (primary, secondary) if c],
        collection_name=test_collection,
    )

    client = FakeEmbeddingClient({"Query Name": entry_vector})  # identical vector -> score 1.0
    hits = screen_name(client, "Query Name", collection_name=test_collection)

    assert len(hits) == 1
    assert hits[0].method == "vector"
    assert hits[0].matched_name == "Entry Name"
    assert hits[0].match_score == pytest.approx(1.0)


def test_orthogonal_vector_below_threshold_is_not_returned_via_vector(
    test_collection: str,
) -> None:
    upsert_entry(
        list_source="TEST-LIST",
        name="Entry Name",
        vector=_unit_vector(0),
        phonetic_codes=["ZZZZ"],  # deliberately non-matching phonetic code
        collection_name=test_collection,
    )

    client = FakeEmbeddingClient({"Unrelated Query": _unit_vector(1)})  # orthogonal -> score 0.0
    hits = screen_name(client, "Unrelated Query", collection_name=test_collection)

    assert hits == []


def test_phonetic_match_surfaces_a_hit_vector_search_alone_would_miss(
    test_collection: str,
) -> None:
    """The actual point of running both methods: an entry with a low-similarity vector but a
    matching phonetic code must still surface as a hit."""
    primary, secondary = phonetic_codes("Mohammed")
    upsert_entry(
        list_source="TEST-LIST",
        name="Mohammed",
        vector=_unit_vector(0),
        phonetic_codes=[c for c in (primary, secondary) if c],
        collection_name=test_collection,
    )

    # Orthogonal vector (would score 0.0, well below threshold) but "Muhammad" shares
    # Mohammed's phonetic code -- must still be caught.
    client = FakeEmbeddingClient({"Muhammad": _unit_vector(1)})
    hits = screen_name(client, "Muhammad", collection_name=test_collection)

    assert len(hits) == 1
    assert hits[0].method == "phonetic"
    assert hits[0].matched_name == "Mohammed"


def test_double_hit_via_both_methods_is_deduplicated_to_one_row(test_collection: str) -> None:
    primary, secondary = phonetic_codes("Entry Name")
    entry_vector = _unit_vector(0)
    upsert_entry(
        list_source="TEST-LIST",
        name="Entry Name",
        vector=entry_vector,
        phonetic_codes=[c for c in (primary, secondary) if c],
        collection_name=test_collection,
    )

    # Same vector (vector-match) AND same name, so same phonetic code (phonetic-match too).
    client = FakeEmbeddingClient({"Entry Name": entry_vector})
    hits = screen_name(client, "Entry Name", collection_name=test_collection)

    assert len(hits) == 1  # not two rows for the same underlying entry
    assert hits[0].method == "vector"  # vector search runs first, so it wins the dedup


def test_no_match_returns_empty_list(test_collection: str) -> None:
    upsert_entry(
        list_source="TEST-LIST",
        name="Entry Name",
        vector=_unit_vector(0),
        phonetic_codes=["ZZZZ"],
        collection_name=test_collection,
    )
    client = FakeEmbeddingClient({"No Relation At All": _unit_vector(5)})
    assert screen_name(client, "No Relation At All", collection_name=test_collection) == []


def test_real_gemini_and_qdrant_end_to_end(test_collection: str) -> None:
    """The one real test: actual Gemini embeddings against actual Qdrant, proving the real
    integration works, not just the orchestration logic against fakes."""
    from services.screening.embeddings import GeminiEmbeddingClient

    client = GeminiEmbeddingClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_embedding_model,
        dimensions=settings.embedding_dimensions,
    )

    primary, secondary = phonetic_codes("Mohammed Al-Rashid")
    upsert_entry(
        list_source="TEST-LIST",
        name="Mohammed Al-Rashid",
        vector=client.embed("Mohammed Al-Rashid"),
        phonetic_codes=[c for c in (primary, secondary) if c],
        collection_name=test_collection,
    )

    hits = screen_name(client, "Muhammad Al-Rashid", collection_name=test_collection)

    assert len(hits) == 1
    assert hits[0].matched_name == "Mohammed Al-Rashid"
    assert hits[0].match_score > settings.sanctions_vector_threshold
