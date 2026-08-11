import pytest

from services.pipeline.workflows.task_queues import task_queue_for_plan_tier


@pytest.mark.parametrize("tier", ["standard", "pro", "enterprise"])
def test_known_tiers_get_distinct_queues(tier: str) -> None:
    assert task_queue_for_plan_tier(tier) == f"kyc-case-{tier}"


def test_unknown_tier_raises_rather_than_silently_defaulting() -> None:
    with pytest.raises(ValueError, match="unknown plan_tier"):
        task_queue_for_plan_tier("nonexistent-tier")
