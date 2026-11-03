"""Combines extraction confidence, face-match similarity, and sanctions hits into a single
risk score. Tuned for recall per PLAN.md: any sanctions hit or undetectable face forces review
regardless of how clean everything else looks -- a good combined score can't paper over a
single hard red flag, the same principle Phase 6's simpler any-red-flag gate already
established. What this module adds on top: an actual weighted score for the cases that pass
those hard gates, instead of only ever saying "clean" or "not clean."

Since Phase 13b the weights and threshold come from the tenant's versioned RiskPolicy
(services/common/policy.py) rather than module constants. The hard gates above stay here, ahead
of any policy, because a policy is not allowed to switch them off. evaluate_policy() is pure:
given the same policy and inputs it always returns the same assessment, which is what lets the
evaluation harness score a policy offline (services/eval/risk.py).
"""

import datetime as dt
from dataclasses import dataclass

from services.common.policy import BUILT_IN_POLICY, RiskPolicy


@dataclass
class RiskInputs:
    extraction_confidence: float
    face_match_score: float | None  # None means no face was detected, not a low score
    sanctions_hit_count: int
    # From the extracted document, when the extraction produced one. None means unknown, which
    # no document rule treats as a pass or a fail -- it simply doesn't fire.
    expiry_date: dt.date | None = None
    nationality: str | None = None


@dataclass
class RiskAssessment:
    risk_score: float  # 0.0 (clean) .. 1.0 (high risk)
    needs_review: bool
    reason: str | None


def evaluate_policy(
    policy: RiskPolicy, inputs: RiskInputs, *, today: dt.date | None = None
) -> RiskAssessment:
    # Hard stops: not part of the policy, and checked first so no policy can reach past them.
    if inputs.sanctions_hit_count > 0:
        return RiskAssessment(risk_score=1.0, needs_review=True, reason="sanctions list hit")
    if inputs.face_match_score is None:
        return RiskAssessment(
            risk_score=1.0, needs_review=True, reason="no face detected for comparison"
        )

    rule = policy.auto_clear
    combined = (
        rule.face_match_weight * inputs.face_match_score
        + rule.extraction_weight * inputs.extraction_confidence
    )
    risk_score = round(1.0 - combined, 4)

    today = today or dt.date.today()
    reasons: list[str] = []
    if combined < rule.min_combined:
        reasons.append(
            f"combined confidence {combined:.4f} below auto-clear threshold {rule.min_combined}"
        )
    if policy.min_face_match is not None and inputs.face_match_score < policy.min_face_match:
        reasons.append(
            f"face match {inputs.face_match_score:.4f} below policy minimum {policy.min_face_match}"
        )
    rules = policy.document_rules
    if inputs.expiry_date is not None:
        days_left = (inputs.expiry_date - today).days
        if days_left < 0 and rules.review_expired:
            reasons.append(f"document expired on {inputs.expiry_date.isoformat()}")
        elif days_left >= 0 and rules.expiring_within_days is not None:
            if days_left <= rules.expiring_within_days:
                reasons.append(f"document expires within {rules.expiring_within_days} days")
    if inputs.nationality and inputs.nationality.upper() in policy.review_nationalities:
        reasons.append(f"nationality {inputs.nationality.upper()} requires review under policy")

    return RiskAssessment(
        risk_score=risk_score, needs_review=bool(reasons), reason="; ".join(reasons) or None
    )


def assess_risk(inputs: RiskInputs) -> RiskAssessment:
    """The built-in policy (version 0): the behaviour every case had before Phase 13b."""
    return evaluate_policy(BUILT_IN_POLICY, inputs)
