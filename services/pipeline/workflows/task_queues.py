"""Task-queue-per-plan-tier routing, introduced in PLAN.md Phase 5 so the mechanism existed
from the start rather than being bolted onto a single shared queue after a real incident.

Phase 5 deliberately shipped the routing without proving it isolated load, because a worker
binds to one Temporal task queue at a time: the proof needs capacity actually divided between
queues, not just correct queue names. Phase 12 supplied it. `worker.py --tier` plus
`--max-concurrent-activities` makes a single-tier bounded pool possible, and
`scripts/load_test_noisy_neighbor.py` measures one tenant flooding its own tier while another
submits a steady trickle to a different one, holding total capacity equal across topologies.

Measured p95 degradation of the quiet tenant (48-case burst vs 10 quiet cases, 6 total slots):

    work type   pooled (one queue)   partitioned (per tier)
    I/O-bound          3.74x                 1.14x
    CPU-bound          5.44x                 2.51x

So routing is worth having -- for I/O-bound activities, which is most of the pipeline's real
time (the Gemini call, S3, Postgres round-trips), partitioning the same total capacity across
tier queues takes a 3.7x SLA blowout down to noise.

The CPU-bound row is the one that constrains deployment shape. Partitioning halves the damage
but does not remove it, because `worker.py` runs all three tiers' Workers in **one process**:
separate queues give each tier its own activity slots, but every slot still contends for one
GIL. Local CPU work (MRZ parsing, phonetic coding, image handling) therefore leaks across tier
boundaries no matter how the queues are arranged. Fixing that needs one process per tier, which
is Phase 13's per-pool autoscaling rather than anything configurable here -- and it is why the
`--tier` flag exists even though nothing in `docker compose` passes it yet.
"""

PLAN_TIERS = ("standard", "pro", "enterprise")


def task_queue_for_plan_tier(plan_tier: str) -> str:
    if plan_tier not in PLAN_TIERS:
        raise ValueError(f"unknown plan_tier {plan_tier!r}; expected one of {PLAN_TIERS}")
    return f"kyc-case-{plan_tier}"
