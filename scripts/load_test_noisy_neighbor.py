"""Noisy-neighbor load test (Phase 12, PLAN.md §9): does one tenant's burst blow another
tenant's SLA?

This validates the Phase 5 task-queue-routing design under real load rather than in theory. The
Temporal server, the task queues, `task_queue_for_plan_tier`, and the worker machinery are all
real -- only the work each activity performs is synthetic, because the property under test is
scheduling and capacity isolation. Real Gemini/OCR calls would swamp that signal with API
latency variance and cost money per run.

Two independent variables, because they answer two different questions and only the pair of
them says anything useful:

**Capacity topology** -- how a fixed total number of activity slots is arranged.

  pooled       One queue, one worker, all N slots. Every tenant's work lands in the same
               place. This is the naive design Phase 5 deliberately avoided.
  partitioned  One queue per plan tier, N/3 slots each. Same total capacity, but each tier
               owns its share. This is what Phase 5's routing plus Phase 12's
               `--max-concurrent-activities` makes possible.

Total capacity is held equal across both so the comparison measures *arrangement*, not just
"more slots is faster".

**Work type** -- what an activity does with its slot, which decides whether partitioning slots
is enough on its own.

  sleep  `asyncio.sleep`. Yields the event loop, so slots are the only contended resource.
         This models the pipeline's I/O-bound activities (the Gemini extraction call, DB
         round-trips) -- the majority of real activity time.
  cpu    A busy loop of the same duration. Holds the GIL, so it contends for the *process*
         regardless of which queue dispatched it. This models the pipeline's local-CPU work
         (MRZ parsing, phonetic coding, image handling before upload).

The `cpu` case is the one that matters for deployment shape: if partitioned slots protect the
quiet tenant under `sleep` but not under `cpu`, then routing alone is insufficient and the
tiers need separate *processes*, which is Phase 13's per-pool autoscaling rather than anything
configurable today.

Usage (needs docker compose up + nothing else polling these queues):
    uv run python scripts/load_test_noisy_neighbor.py
    uv run python scripts/load_test_noisy_neighbor.py --work cpu
    uv run python scripts/load_test_noisy_neighbor.py --noisy-cases 60 --quiet-cases 10
"""

import argparse
import asyncio
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from temporalio import activity, workflow  # noqa: E402
from temporalio.client import Client  # noqa: E402
from temporalio.common import RetryPolicy  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from services.pipeline.config import settings  # noqa: E402
from services.pipeline.workflows.task_queues import (
    PLAN_TIERS,  # noqa: E402
    task_queue_for_plan_tier,  # noqa: E402
)

# Long enough that a burst genuinely occupies worker slots, short enough that a run finishes in
# a couple of minutes.
_ACTIVITY_SECONDS = 0.5

# The pooled topology needs a queue of its own rather than borrowing a tier's, so a pooled run
# can't be confused with a partitioned one by anything still polling from a previous run.
_POOLED_QUEUE = "kyc-case-loadtest-pooled"


def _burn_cpu(seconds: float) -> None:
    """Occupy the GIL for `seconds`, the way local image/MRZ work does."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pass


@activity.defn(name="load_test_sleep_work")
async def load_test_sleep_work(seconds: float) -> None:
    await asyncio.sleep(seconds)


@activity.defn(name="load_test_cpu_work")
async def load_test_cpu_work(seconds: float) -> None:
    _burn_cpu(seconds)


_ACTIVITIES = {"sleep": load_test_sleep_work, "cpu": load_test_cpu_work}
_ACTIVITY_NAMES = {"sleep": "load_test_sleep_work", "cpu": "load_test_cpu_work"}


@workflow.defn(name="LoadTestCaseWorkflow", sandboxed=False)
class LoadTestCaseWorkflow:
    @workflow.run
    async def run(self, activity_name: str, seconds: float) -> None:
        await workflow.execute_activity(
            activity_name,
            seconds,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@dataclass
class Latencies:
    samples: list[float]

    @property
    def p50(self) -> float:
        return statistics.median(self.samples) if self.samples else float("nan")

    @property
    def p95(self) -> float:
        if not self.samples:
            return float("nan")
        ordered = sorted(self.samples)
        return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]

    @property
    def worst(self) -> float:
        return max(self.samples) if self.samples else float("nan")


def _queue_for(topology: str, tier: str) -> str:
    """Where a tenant's work goes. The whole difference between the topologies is here."""
    return _POOLED_QUEUE if topology == "pooled" else task_queue_for_plan_tier(tier)


async def _run_one(
    client: Client, topology: str, work: str, tier: str, run_id: str, index: int
) -> float:
    """End-to-end latency for one workflow: submit -> completed."""
    started = time.monotonic()
    await client.execute_workflow(
        "LoadTestCaseWorkflow",
        args=[_ACTIVITY_NAMES[work], _ACTIVITY_SECONDS],
        id=f"loadtest-{run_id}-{tier}-{index}",
        task_queue=_queue_for(topology, tier),
    )
    return time.monotonic() - started


async def _measure_quiet_tenant(
    client: Client,
    topology: str,
    work: str,
    run_id: str,
    quiet_tier: str,
    quiet_cases: int,
    label: str,
) -> Latencies:
    results = await asyncio.gather(
        *(
            _run_one(client, topology, work, quiet_tier, f"{run_id}-{label}", i)
            for i in range(quiet_cases)
        )
    )
    return Latencies(list(results))


def _build_workers(client: Client, topology: str, work: str, total_slots: int) -> list[Worker]:
    """Arrange `total_slots` activity slots according to `topology`.

    Total capacity is identical either way; only its division differs. That is the point --
    a pooled topology that simply had fewer slots would be a rigged comparison.
    """
    activities = [_ACTIVITIES[work]]
    if topology == "pooled":
        return [
            Worker(
                client,
                task_queue=_POOLED_QUEUE,
                workflows=[LoadTestCaseWorkflow],
                activities=activities,
                max_concurrent_activities=total_slots,
            )
        ]

    # partitioned: each tier gets a dedicated, equal share of the same total.
    per_tier = max(1, total_slots // len(PLAN_TIERS))
    return [
        Worker(
            client,
            task_queue=task_queue_for_plan_tier(tier),
            workflows=[LoadTestCaseWorkflow],
            activities=activities,
            max_concurrent_activities=per_tier,
        )
        for tier in PLAN_TIERS
    ]


async def _run_experiment(
    client: Client,
    topology: str,
    work: str,
    noisy_cases: int,
    quiet_cases: int,
    total_slots: int,
    noisy_tier: str,
    quiet_tier: str,
) -> tuple[Latencies, Latencies]:
    run_id = uuid.uuid4().hex[:8]
    workers = _build_workers(client, topology, work, total_slots)

    stack: list[Worker] = []
    worker_tasks: list[asyncio.Task[None]] = []
    try:
        for worker in workers:
            await worker.__aenter__()
            stack.append(worker)
            worker_tasks.append(asyncio.create_task(worker.run()))

        # Baseline: the quiet tenant alone, nothing else on any queue.
        baseline = await _measure_quiet_tenant(
            client, topology, work, run_id, quiet_tier, quiet_cases, "baseline"
        )

        # Contended: the noisy tenant floods its own tier while the quiet tenant submits the
        # identical workload it just measured alone.
        noisy = [
            asyncio.create_task(_run_one(client, topology, work, noisy_tier, f"{run_id}-noisy", i))
            for i in range(noisy_cases)
        ]
        await asyncio.sleep(1.0)  # let the burst actually fill the queue first
        contended = await _measure_quiet_tenant(
            client, topology, work, run_id, quiet_tier, quiet_cases, "contended"
        )
        await asyncio.gather(*noisy, return_exceptions=True)
    finally:
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        for worker in reversed(stack):
            await worker.__aexit__(None, None, None)

    return baseline, contended


def _report(topology: str, work: str, baseline: Latencies, contended: Latencies) -> float:
    degradation = contended.p95 / baseline.p95 if baseline.p95 else float("nan")
    print(f"\n--- topology: {topology}, work: {work} ---")
    print(f"  quiet tenant, no neighbour   : p50={baseline.p50:6.2f}s  p95={baseline.p95:6.2f}s")
    print(f"  quiet tenant, noisy neighbour: p50={contended.p50:6.2f}s  p95={contended.p95:6.2f}s")
    print(f"  p95 degradation factor       : {degradation:.2f}x")
    return degradation


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--noisy-cases", type=int, default=60)
    parser.add_argument("--quiet-cases", type=int, default=10)
    parser.add_argument(
        "--total-slots",
        type=int,
        default=6,
        help=(
            "Total activity slots, held equal across topologies. Low on purpose: contention is "
            "the thing being measured, and it must be divisible by the number of plan tiers."
        ),
    )
    parser.add_argument("--work", choices=sorted(_ACTIVITIES), default="sleep")
    parser.add_argument("--noisy-tier", default="standard")
    parser.add_argument("--quiet-tier", default="enterprise")
    args = parser.parse_args()

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)

    print(
        f"noisy tenant: {args.noisy_cases} cases on '{args.noisy_tier}'\n"
        f"quiet tenant: {args.quiet_cases} cases on '{args.quiet_tier}'\n"
        f"work type: {args.work}, activity duration: {_ACTIVITY_SECONDS}s\n"
        f"total slots: {args.total_slots} "
        f"(pooled: all {args.total_slots} on one queue; "
        f"partitioned: {max(1, args.total_slots // len(PLAN_TIERS))} per tier)"
    )

    results = {}
    for topology in ("pooled", "partitioned"):
        baseline, contended = await _run_experiment(
            client,
            topology,
            args.work,
            args.noisy_cases,
            args.quiet_cases,
            args.total_slots,
            args.noisy_tier,
            args.quiet_tier,
        )
        results[topology] = _report(topology, args.work, baseline, contended)

    print(f"\n=== finding ({args.work} work) ===")
    for topology, degradation in results.items():
        verdict = "isolated" if degradation < 2.0 else "DEGRADED"
        print(f"  {topology:12s}: {degradation:5.2f}x p95 -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
