import pytest

from services.pipeline.risk_scoring import RiskInputs, assess_risk


def test_sanctions_hit_forces_review_regardless_of_other_scores() -> None:
    result = assess_risk(
        RiskInputs(extraction_confidence=1.0, face_match_score=1.0, sanctions_hit_count=1)
    )

    assert result.needs_review is True
    assert result.risk_score == 1.0
    assert result.reason == "sanctions list hit"


def test_no_face_detected_forces_review_regardless_of_other_scores() -> None:
    result = assess_risk(
        RiskInputs(extraction_confidence=1.0, face_match_score=None, sanctions_hit_count=0)
    )

    assert result.needs_review is True
    assert result.risk_score == 1.0
    assert result.reason == "no face detected for comparison"


def test_high_confidence_and_high_face_match_auto_clears() -> None:
    result = assess_risk(
        RiskInputs(extraction_confidence=0.95, face_match_score=0.95, sanctions_hit_count=0)
    )

    assert result.needs_review is False
    assert result.reason is None
    assert result.risk_score == pytest.approx(0.05, abs=0.01)


def test_low_confidence_and_low_face_match_needs_review() -> None:
    result = assess_risk(
        RiskInputs(extraction_confidence=0.5, face_match_score=0.5, sanctions_hit_count=0)
    )

    assert result.needs_review is True
    assert result.risk_score == pytest.approx(0.5, abs=0.01)
    assert result.reason is not None
    assert "below auto-clear threshold" in result.reason


def test_face_match_weighted_higher_than_extraction_confidence() -> None:
    # Same average (0.7) but weighted toward the weaker face match should score worse than
    # weighted toward the weaker extraction confidence, since face match carries more weight.
    weak_face = assess_risk(
        RiskInputs(extraction_confidence=0.95, face_match_score=0.45, sanctions_hit_count=0)
    )
    weak_extraction = assess_risk(
        RiskInputs(extraction_confidence=0.45, face_match_score=0.95, sanctions_hit_count=0)
    )

    assert weak_face.risk_score > weak_extraction.risk_score


def test_sanctions_hit_takes_priority_over_missing_face() -> None:
    result = assess_risk(
        RiskInputs(extraction_confidence=1.0, face_match_score=None, sanctions_hit_count=3)
    )

    assert result.reason == "sanctions list hit"


@pytest.mark.parametrize("confidence", [0.849, 0.85, 0.851])
def test_auto_clear_threshold_boundary(confidence: float) -> None:
    # face_match_score == confidence so combined == confidence exactly (0.6+0.4 weights sum to 1)
    result = assess_risk(
        RiskInputs(
            extraction_confidence=confidence, face_match_score=confidence, sanctions_hit_count=0
        )
    )

    assert result.needs_review is (confidence < 0.85)
