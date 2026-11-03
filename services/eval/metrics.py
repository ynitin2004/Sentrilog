"""The metrics, as plain functions over plain numbers, so each one can be checked by hand in
tests/eval/test_metrics.py."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FieldCounts:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0

    def __add__(self, other: "FieldCounts") -> "FieldCounts":
        return FieldCounts(
            self.true_positive + other.true_positive,
            self.false_positive + other.false_positive,
            self.false_negative + other.false_negative,
        )

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def field_counts(expected: str | None, actual: str | None) -> FieldCounts:
    """One extracted field against ground truth. A wrong value is both a false positive (it
    asserted something untrue) and a false negative (the true value was not produced) -- the
    standard field-level scoring for information extraction. A missing value is only a miss."""
    if expected is None and actual is None:
        return FieldCounts()
    if expected is None:
        return FieldCounts(false_positive=1)
    if actual is None:
        return FieldCounts(false_negative=1)
    if expected == actual:
        return FieldCounts(true_positive=1)
    return FieldCounts(false_positive=1, false_negative=1)


def rate_at_or_above(scores: Sequence[float] | np.ndarray, threshold: float) -> float:
    values = np.asarray(scores, dtype=np.float64)
    return float((values >= threshold).mean()) if values.size else 0.0


def tar_at_far(
    genuine: Sequence[float], impostor: Sequence[float], far: float
) -> tuple[float, float]:
    """True accept rate at the threshold where the false accept rate is at most `far`.

    Returns (tar, threshold). The threshold is the smallest score that keeps the impostor accept
    rate at or under `far`, so it is a property of the impostor set: with N impostor pairs, a FAR
    below 1/N cannot be resolved, and the caller must not report one (see face.py).
    """
    imp = np.sort(np.asarray(impostor, dtype=np.float64))[::-1]
    gen = np.asarray(genuine, dtype=np.float64)
    allowed = int(np.floor(far * imp.size))
    # Accept iff score > threshold. Placing the threshold at the (allowed+1)-th highest impostor
    # score lets exactly `allowed` impostors through.
    threshold = float(imp[allowed]) if allowed < imp.size else float("-inf")
    return float((gen > threshold).mean()) if gen.size else 0.0, threshold
