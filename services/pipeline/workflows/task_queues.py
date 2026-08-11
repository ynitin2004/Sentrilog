"""Task-queue-per-plan-tier routing. Design only for now, per PLAN.md Phase 5 -- load-testing
that one tenant's backlog can't starve another's (the actual point of this) is Phase 9's job,
not this phase's. What matters here is that the routing mechanism exists from the start rather
than being bolted onto a single shared queue after a real incident.

A worker binds to one Temporal task queue at a time; running the noisy-neighbor test for real
means running separate worker pools per queue with independently sized capacity. Locally, one
worker process polls all of them concurrently (see workflows/worker.py) -- that proves routing
works, not that it isolates load, which is exactly what's deferred to Phase 9.
"""

PLAN_TIERS = ("standard", "pro", "enterprise")


def task_queue_for_plan_tier(plan_tier: str) -> str:
    if plan_tier not in PLAN_TIERS:
        raise ValueError(f"unknown plan_tier {plan_tier!r}; expected one of {PLAN_TIERS}")
    return f"kyc-case-{plan_tier}"
