"""The synthetic passport generator produces MRZs the production parser accepts and reads back
exactly, and the risk suite's arithmetic matches a case worked by hand."""

import random
from typing import Any

import pytest

from services.eval import extraction, risk
from services.eval.synthetic_id import build_td3, random_identity
from services.pipeline.extraction.mrz import parse_td3


def test_generated_mrz_is_valid_and_round_trips() -> None:
    rng = random.Random(1)
    for _ in range(50):
        identity = random_identity(rng)
        line1, line2 = build_td3(identity)
        assert len(line1) == len(line2) == 44
        result = parse_td3(line1, line2)
        assert result.valid, result.failed_checks
        assert result.full_name == identity.full_name
        assert result.document_number == identity.document_number
        assert result.date_of_birth == identity.date_of_birth
        assert result.expiry_date == identity.expiry_date
        assert result.nationality == identity.nationality


def test_uncorrupted_mrz_parse_path_scores_perfectly() -> None:
    ids = extraction.identities(40)
    original = extraction.CORRUPTION_RATE
    try:
        extraction.CORRUPTION_RATE = 0.0
        result = extraction.evaluate_mrz_parse(ids)
    finally:
        extraction.CORRUPTION_RATE = original
    assert (result.accepted, result.wrong_but_accepted, result.f1) == (40, 0, 1.0)


def test_dataset_hash_is_stable() -> None:
    assert extraction.dataset_sha256(extraction.identities(5)) == extraction.dataset_sha256(
        extraction.identities(5)
    )


def test_risk_composition_by_hand() -> None:
    # Production rule: auto-clear iff 0.6*face + 0.4*0.99 >= 0.85, i.e. face >= 0.7567.
    result = risk.evaluate(
        genuine_scores=[0.9, 0.8, 0.5, 0.3],  # half clear on face
        impostor_scores=[0.1, 0.2, 0.8, 0.05],  # a quarter clear on face
        screening_recall=0.9,
        screening_fpr=0.1,
    )
    rates = result.auto_clear_rate_by_kind
    assert rates["genuine_clear"] == pytest.approx(0.5 * 0.9)
    assert rates["impostor_clear"] == pytest.approx(0.25 * 0.9)
    assert rates["genuine_listed"] == pytest.approx(0.5 * 0.1)
    cleared = {k: risk.PRIORS[k] * rates[k] for k in risk.PRIORS}
    assert result.auto_clear_precision == pytest.approx(
        cleared["genuine_clear"] / sum(cleared.values())
    )
    assert result.review_burden == pytest.approx(1 - sum(cleared.values()))


def _result(
    embedding: str, reranker: str | None, fpr: float, recall: float = 0.96
) -> dict[str, Any]:
    return {
        "embedding": embedding,
        "reranker": reranker,
        "target_met": recall >= 0.95,
        "chosen": {"false_positive_rate": fpr, "recall": recall},
    }


def test_select_shipped_needs_a_reranker_to_beat_two_standard_errors() -> None:
    from services.eval.screening import select_shipped, standard_error

    # 2,000 negatives in the pinned set: at FPR 0.13 one standard error is ~0.0075.
    se = standard_error(0.13, 2000)
    plain = _result("ngram", None, 0.13)
    small_lift = _result("ngram", "x", 0.13 - se)
    big_lift = _result("ngram", "x", 0.13 - 3 * se)
    other = _result("bge", None, 0.20)
    assert select_shipped([plain, small_lift, other]) is plain
    assert select_shipped([plain, big_lift, other]) is big_lift
    # A configuration that misses the recall target never ships while one meets it.
    assert select_shipped([_result("lowfpr", None, 0.01, recall=0.90), other]) is other
