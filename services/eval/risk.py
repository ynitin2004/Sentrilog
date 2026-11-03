"""Risk-policy evaluation: auto-clear precision and review burden (improvements.md P-2, P-3).

There is no labelled set of real KYC cases, so this composes one from what the other suites
measured, and says so in every report:

- face match: the genuine and impostor similarity scores from the face suite (LFW DevTest);
- screening: recall and false-positive rate at the shipped configuration, from the screening
  suite;
- extraction: the MRZ path's confidence (0.99) for documents it accepts. VLM-path confidences are
  not measured in CI, so cases that would take that path are out of scope here.

Each case is one of four kinds, mixed at stated priors (PRIORS). For each kind the suite applies
the production risk function to every measured face score and the measured screening rates, and
computes exactly -- no sampling -- how often a case of that kind is auto-cleared. From those:

- auto-clear precision: of the cases auto-cleared, the share a correct reviewer would also have
  approved (genuine person, not listed);
- review burden: the share of all cases sent to a human;
- leak rates: P(auto-clear | impostor) and P(auto-clear | listed person) -- the two failures
  that matter, independent of the priors.

Precision depends on the priors; the leak rates do not. Both are reported.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from services.common.policy import RiskPolicy
from services.pipeline.risk_scoring import RiskInputs, assess_risk, evaluate_policy

MRZ_CONFIDENCE = 0.99

# Fractions of incoming cases. Stated, not measured: a deployment's real mix will differ, which
# moves precision and review burden but not the leak rates.
PRIORS = {
    "genuine_clear": 0.94,  # the right person, not on any list
    "impostor_clear": 0.05,  # someone else's selfie or document
    "genuine_listed": 0.01,  # the right person, and on the sanctions list
}

Decider = Callable[[RiskInputs], bool]  # True when the case is auto-cleared


def production_decider(inputs: RiskInputs) -> bool:
    return not assess_risk(inputs).needs_review


def policy_decider(policy: RiskPolicy) -> Decider:
    """The same evaluator the risk activity runs, under a candidate tenant policy -- so a policy
    can be measured before anyone adopts it."""
    return lambda inputs: not evaluate_policy(policy, inputs).needs_review


@dataclass
class RiskResult:
    policy: str
    priors: dict[str, float]
    auto_clear_rate_by_kind: dict[str, float]
    auto_clear_precision: float
    review_burden: float
    leak_rate_impostor: float
    leak_rate_listed: float
    inputs: dict[str, Any]
    caveat: str


def _auto_clear_rate(face_scores: list[float], p_screen_clear: float, decide: Decider) -> float:
    """P(auto-clear) for a case kind: the face score varies over the measured distribution;
    screening independently returns no hit with probability p_screen_clear (a hit always goes
    to review, D11)."""
    if not face_scores:
        return 0.0
    cleared = sum(
        decide(
            RiskInputs(
                extraction_confidence=MRZ_CONFIDENCE, face_match_score=s, sanctions_hit_count=0
            )
        )
        for s in face_scores
    )
    return (cleared / len(face_scores)) * p_screen_clear


def evaluate(
    *,
    genuine_scores: list[float],
    impostor_scores: list[float],
    screening_recall: float,
    screening_fpr: float,
    decide: Decider = production_decider,
    policy: str = "built-in, version 0 (services/common/policy.py)",
) -> RiskResult:
    rates = {
        "genuine_clear": _auto_clear_rate(genuine_scores, 1 - screening_fpr, decide),
        "impostor_clear": _auto_clear_rate(impostor_scores, 1 - screening_fpr, decide),
        "genuine_listed": _auto_clear_rate(genuine_scores, 1 - screening_recall, decide),
    }
    cleared = {k: PRIORS[k] * rates[k] for k in PRIORS}
    total_cleared = sum(cleared.values())
    return RiskResult(
        policy=policy,
        priors=PRIORS,
        auto_clear_rate_by_kind=rates,
        auto_clear_precision=cleared["genuine_clear"] / total_cleared if total_cleared else 0.0,
        review_burden=1 - total_cleared,
        leak_rate_impostor=rates["impostor_clear"],
        leak_rate_listed=rates["genuine_listed"],
        inputs={
            "face_genuine_pairs": len(genuine_scores),
            "face_impostor_pairs": len(impostor_scores),
            "screening_recall": screening_recall,
            "screening_false_positive_rate": screening_fpr,
            "extraction_confidence": MRZ_CONFIDENCE,
        },
        caveat=(
            "Composed from component measurements at stated priors, not observed on real "
            "cases; MRZ-path cases only."
        ),
    )


def as_dict(result: RiskResult) -> dict[str, Any]:
    return asdict(result)
