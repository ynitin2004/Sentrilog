"""The risk policy document and its evaluator (Phase 13b): what a tenant may express, what it
may not, and that the hard stops hold whatever the policy says."""

import datetime as dt

import pytest
from pydantic import ValidationError

from services.common.policy import BUILT_IN_POLICY, AutoClear, DocumentRules, RiskPolicy
from services.pipeline.risk_scoring import RiskInputs, evaluate_policy

_TODAY = dt.date(2026, 11, 3)


def _policy(**overrides: object) -> RiskPolicy:
    base: dict[str, object] = {
        "auto_clear": {"min_combined": 0.5, "face_match_weight": 0.5, "extraction_weight": 0.5}
    }
    return RiskPolicy.model_validate({**base, **overrides})


def _clean(**overrides: object) -> RiskInputs:
    values: dict[str, object] = {
        "extraction_confidence": 0.99,
        "face_match_score": 0.95,
        "sanctions_hit_count": 0,
    }
    return RiskInputs(**{**values, **overrides})  # type: ignore[arg-type]


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError):
        AutoClear(min_combined=0.8, face_match_weight=0.7, extraction_weight=0.4)


def test_a_policy_cannot_express_rejection_or_disable_hard_stops() -> None:
    # extra="forbid": an unknown action key is an error, not silently ignored. There is no
    # field anywhere that could say "reject" (D11) or "skip sanctions".
    for extra in ({"on_sanctions_hit": "approve"}, {"expired": "reject"}):
        with pytest.raises(ValidationError):
            _policy(document_rules=extra)
    with pytest.raises(ValidationError):
        _policy(hard_stops=[])


def test_nationalities_are_normalised_and_validated() -> None:
    assert _policy(review_nationalities=["irn", "PRK ", "irn"]).review_nationalities == [
        "IRN",
        "PRK",
    ]
    with pytest.raises(ValidationError):
        _policy(review_nationalities=["Iran"])


def test_hard_stops_hold_under_the_most_permissive_policy() -> None:
    lenient = _policy(
        auto_clear={"min_combined": 0.01, "face_match_weight": 1.0, "extraction_weight": 0.0}
    )
    hit = evaluate_policy(lenient, _clean(sanctions_hit_count=1), today=_TODAY)
    no_face = evaluate_policy(lenient, _clean(face_match_score=None), today=_TODAY)
    assert hit.needs_review and hit.reason == "sanctions list hit"
    assert no_face.needs_review and no_face.reason == "no face detected for comparison"


def test_min_face_match_overrides_a_clearing_combined_score() -> None:
    policy = _policy(min_face_match=0.6)
    assessment = evaluate_policy(policy, _clean(face_match_score=0.55), today=_TODAY)
    # 0.5*0.55 + 0.5*0.99 = 0.77 clears min_combined 0.5, but the face floor does not.
    assert assessment.needs_review
    assert assessment.reason is not None and "below policy minimum 0.6" in assessment.reason


def test_document_rules() -> None:
    policy = _policy(document_rules={"review_expired": True, "expiring_within_days": 30})
    expired = evaluate_policy(policy, _clean(expiry_date=dt.date(2026, 11, 2)), today=_TODAY)
    soon = evaluate_policy(policy, _clean(expiry_date=dt.date(2026, 12, 1)), today=_TODAY)
    later = evaluate_policy(policy, _clean(expiry_date=dt.date(2027, 6, 1)), today=_TODAY)
    unknown = evaluate_policy(policy, _clean(expiry_date=None), today=_TODAY)
    assert expired.needs_review and "expired" in (expired.reason or "")
    assert soon.needs_review and "within 30 days" in (soon.reason or "")
    assert not later.needs_review
    assert not unknown.needs_review  # unknown fires no rule


def test_review_nationalities() -> None:
    policy = _policy(review_nationalities=["PRK"])
    assert evaluate_policy(policy, _clean(nationality="prk"), today=_TODAY).needs_review
    assert not evaluate_policy(policy, _clean(nationality="FRA"), today=_TODAY).needs_review


def test_built_in_policy_is_the_pre_policy_behaviour() -> None:
    # Version 0 must decide exactly as risk_scoring.py's constants did, including not looking
    # at document expiry, which the code before Phase 13b never did.
    assert BUILT_IN_POLICY.auto_clear == AutoClear(
        min_combined=0.85, face_match_weight=0.6, extraction_weight=0.4
    )
    assert BUILT_IN_POLICY.document_rules == DocumentRules(review_expired=False)
    expired = _clean(expiry_date=dt.date(2000, 1, 1))
    assert not evaluate_policy(BUILT_IN_POLICY, expired, today=_TODAY).needs_review
