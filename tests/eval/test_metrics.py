"""The harness's arithmetic, checked against values worked out by hand. A metric that is wrong
makes every number built on it wrong, and nothing downstream would notice."""

import pytest

from services.eval.metrics import FieldCounts, field_counts, rate_at_or_above, tar_at_far


def test_field_counts_score_a_wrong_value_as_both_a_false_positive_and_a_miss() -> None:
    assert field_counts("A", "A") == FieldCounts(true_positive=1)
    assert field_counts("A", "B") == FieldCounts(false_positive=1, false_negative=1)
    assert field_counts("A", None) == FieldCounts(false_negative=1)
    assert field_counts(None, "B") == FieldCounts(false_positive=1)
    assert field_counts(None, None) == FieldCounts()


def test_precision_recall_f1_by_hand() -> None:
    # 8 right, 2 wrong (each FP+FN), 2 missing: P = 8/10, R = 8/12, F1 = 2PR/(P+R) = 0.7273.
    counts = FieldCounts(true_positive=8, false_positive=2, false_negative=4)
    assert counts.precision == pytest.approx(0.8)
    assert counts.recall == pytest.approx(8 / 12)
    assert counts.f1 == pytest.approx(2 * 0.8 * (8 / 12) / (0.8 + 8 / 12))
    assert FieldCounts().f1 == 0.0


def test_rate_at_or_above_includes_the_threshold() -> None:
    assert rate_at_or_above([0.1, 0.5, 0.5, 0.9], 0.5) == pytest.approx(0.75)
    assert rate_at_or_above([], 0.5) == 0.0


def test_tar_at_far_lets_through_exactly_the_allowed_impostors() -> None:
    impostor = [i / 100 for i in range(100)]  # 0.00 .. 0.99
    genuine = [0.50, 0.97, 0.985, 0.995]
    # FAR 0.02 over 100 impostors allows 2 through (0.99, 0.98): threshold is 0.97, the third
    # highest, and acceptance is strictly above it.
    tar, threshold = tar_at_far(genuine, impostor, 0.02)
    assert threshold == pytest.approx(0.97)
    assert tar == pytest.approx(0.5)  # 0.985 and 0.995 accepted; 0.97 itself is not


def test_tar_at_far_below_resolution_admits_no_impostor() -> None:
    tar, threshold = tar_at_far([0.8, 0.95], [0.1, 0.9], 0.001)
    assert threshold == pytest.approx(0.9)
    assert tar == pytest.approx(0.5)
