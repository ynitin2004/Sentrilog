"""Combines extraction confidence, face-match similarity, and sanctions hits into a single
risk score. Tuned for recall per PLAN.md: any sanctions hit or undetectable face forces review
regardless of how clean everything else looks -- a good combined score can't paper over a
single hard red flag, the same principle Phase 6's simpler any-red-flag gate already
established. What this module adds on top: an actual weighted score for the cases that pass
those hard gates, instead of only ever saying "clean" or "not clean."
"""

from dataclasses import dataclass

# Combined confidence at/above this auto-clears the case; below it, a human reviews. Face
# match is weighted higher than extraction confidence -- "is this the same person" is the core
# KYC question; a slightly-uncertain field extraction on an otherwise-matched, non-hit case is
# a softer signal.
_FACE_MATCH_WEIGHT = 0.6
_EXTRACTION_CONFIDENCE_WEIGHT = 0.4
_AUTO_CLEAR_THRESHOLD = 0.85


@dataclass
class RiskInputs:
    extraction_confidence: float
    face_match_score: float | None  # None means no face was detected, not a low score
    sanctions_hit_count: int


@dataclass
class RiskAssessment:
    risk_score: float  # 0.0 (clean) .. 1.0 (high risk)
    needs_review: bool
    reason: str | None


def assess_risk(inputs: RiskInputs) -> RiskAssessment:
    if inputs.sanctions_hit_count > 0:
        return RiskAssessment(risk_score=1.0, needs_review=True, reason="sanctions list hit")

    if inputs.face_match_score is None:
        return RiskAssessment(
            risk_score=1.0, needs_review=True, reason="no face detected for comparison"
        )

    combined = (
        _FACE_MATCH_WEIGHT * inputs.face_match_score
        + _EXTRACTION_CONFIDENCE_WEIGHT * inputs.extraction_confidence
    )
    risk_score = round(1.0 - combined, 4)
    needs_review = combined < _AUTO_CLEAR_THRESHOLD
    reason = (
        None
        if not needs_review
        else (
            f"combined confidence {combined:.4f} below "
            f"auto-clear threshold {_AUTO_CLEAR_THRESHOLD}"
        )
    )
    return RiskAssessment(risk_score=risk_score, needs_review=needs_review, reason=reason)
