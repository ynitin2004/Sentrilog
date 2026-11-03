"""decide_hits() is the one place a screening threshold is applied, in production and in the
evaluation harness alike, so its rules are pinned here case by case."""

from services.screening.screen import Candidate, ScreeningConfig, decide_hits


def _c(score: float | None, phonetic: bool = False, rerank: float | None = None) -> Candidate:
    return Candidate(
        "L", "name", "1", "v1", vector_score=score, phonetic=phonetic, rerank_score=rerank
    )


def test_vector_hit_at_and_above_threshold_only() -> None:
    config = ScreeningConfig(vector_threshold=0.8)
    assert [h.method for h in decide_hits([_c(0.8)], config)] == ["vector"]
    assert decide_hits([_c(0.79)], config) == []


def test_phonetic_match_is_a_hit_at_any_vector_score_when_enabled() -> None:
    assert [h.method for h in decide_hits([_c(0.1, phonetic=True)], ScreeningConfig(0.8))] == [
        "phonetic"
    ]
    assert decide_hits([_c(0.1, phonetic=True)], ScreeningConfig(0.8, use_phonetic=False)) == []


def test_reranker_vetoes_but_never_adds() -> None:
    config = ScreeningConfig(vector_threshold=0.8, rerank_threshold=0.0)
    assert decide_hits([_c(0.9, rerank=-1.0)], config) == []
    assert len(decide_hits([_c(0.9, rerank=0.5)], config)) == 1
    assert decide_hits([_c(0.5, rerank=9.0)], config) == []  # below the vector threshold


def test_hit_carries_identity_and_version() -> None:
    (hit,) = decide_hits([_c(0.95)], ScreeningConfig(0.8))
    assert (hit.entity_id, hit.list_version, hit.match_score) == ("1", "v1", 0.95)
