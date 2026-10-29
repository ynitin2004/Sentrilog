"""Noisy-neighbor load test (Phase 12, PLAN.md §9): does one tenant's burst blow another
tenant's SLA?

This validates the Phase 5 task-queue-routing design under real load rather than in theory.
It runs the same experiment against two worker topologies and compares them:

  shared  -- one process polling every plan tier's task queue, which is what
             `python -m services.pipeline.workflows.worker` has shipped since Phase 5.
  per-tier -- one bounded worker per tier, which is what a real deployment runs and what the
             `--tier` / `--max-concurrent-activities` flags added in Phase 12 make possible.

Deliberately synthetic work, not the real KYC activities: the property under test is scheduling
and capacity isolation, so a fixed-duration activity is better science than real Gemini/OCR
calls, which would swamp the signal with API latency variance and cost real money per run. The
task queues, the routing function, the Temporal server, and the worker machinery are all real --
only the work each activity performs is a sleep of known length.

Usage (needs docker compose up + nothing else running on these queues):
    uv run python scripts/load_test_noisy_neighbor.py
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
from services.pipeline.workflows.task_queues import task_queue_for_plan_tier  # noqa: E402

# Long enough that a burst genuinely occupies worker slots, short enough that a run finishes in
# a couple of minutes.
_ACTIVITY_SECONDS = 0.5


@activity.defn(name="load_test_unit_of_work")
async def load_test_unit_of_work(seconds: float) -> None:
    await asyncio.sleep(seconds)


@workflow.defn(name="LoadTestCaseWorkflow", sandboxed=False)
class LoadTestCaseWorkflow:
    @workflow.run
    async def run(self, seconds: float) -> None:
        await workflow.execute_activity(
            "load_test_unit_of_work",
            seconds,
            start_to_close_timeout=timedelta(seconds=60),
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


async def _run_one(client: Client, tier: str, run_id: str, index: int) -> float:
    """End-to-end latency for one workflow: submit -> completed."""
    started = time.monotonic()
    await client.execute_workflow(
        "LoadTestCaseWorkflow",
        _ACTIVITY_SECONDS,
        id=f"loadtest-{run_id}-{tier}-{index}",
        task_queue=task_queue_for_plan_tier(tier),
    )
    return time.monotonic() - started


async def _measure_quiet_tenant(
    client: Client,
    run_id: str,
    quiet_tier: str,
    quiet_cases: int,
    label: str,
) -> Latencies:
    results = await asyncio.gather(
        *(_run_one(client, quiet_tier, f"{run_id}-{label}", i) for i in range(quiet_cases))
    )
    return Latencies(list(results))


def _build_workers(client: Client, topology: str, max_concurrent: int) -> list[Worker]:
    tiers = ("standard", "pro", "enterprise")
    if topology == "shared":
        # Today's shipped topology: one process, one Worker per queue, all sharing this
        # process's event loop and resource limits.
        return [
            Worker(
                client,
                task_queue=task_queue_for_plan_tier(tier),
                workflows=[LoadTestCaseWorkflow],
                activities=[load_test_unit_of_work],
                max_concurrent_activities=max_concurrent,
            )
            for tier in tiers
        ]
    # per-tier: each tier gets its own independently-sized capacity. Still one process here so
    # the comparison isolates *capacity* allocation rather than confounding it with OS-level
    # process scheduling, but this is the shape a real per-pool deployment has.
    return [
        Worker(
            client,
            task_queue=task_queue_for_plan_tier(tier),
            workflows=[LoadTestCaseWorkflow],
            activities=[load_test_unit_of_work],
            max_concurrent_activities=max_concurrent,
        )
        for tier in tiers
    ]


async def _run_experiment(
    client: Client,
    topology: str,
    noisy_cases: int,
    quiet_cases: int,
    max_concurrent: int,
    noisy_tier: str,
    quiet_tier: str,
) -> tuple[Latencies, Latencies]:
    run_id = uuid.uuid4().hex[:8]
    workers = _build_workers(client, topology, max_concurrent)

    async with workers[0], workers[1], workers[2]:
        worker_tasks = [asyncio.create_task(w.run()) for w in workers]
        try:
            # Baseline: the quiet tenant alone, nothing else on any queue.
            baseline = await _measure_quiet_tenant(
                client, run_id, quiet_tier, quiet_cases, "baseline"
            )

            # Contended: the noisy tenant floods its own tier while the quiet tenant submits
            # the identical workload to a different tier.
            noisy = [
                asyncio.create_task(_run_one(client, noisy_tier, f"{run_id}-noisy", i))
                for i in range(noisy_cases)
            ]
            await asyncio.sleep(1.0)  # let the burst actually fill the queue first
            contended = await _measure_quiet_tenant(
                client, run_id, quiet_tier, quiet_cases, "contended"
            )
            await asyncio.gather(*noisy)
        finally:
            for task in worker_tasks:
                task.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)

    return baseline, contended


def _report(topology: str, baseline: Latencies, contended: Latencies) -> float:
    degradation = contended.p95 / baseline.p95 if baseline.p95 else float("nan")
    print(f"\n--- topology: {topology} ---")
    print(f"  quiet tenant, no neighbour : p50={baseline.p50:6.2f}s  p95={baseline.p95:6.2f}s")
    print(f"  quiet tenant, noisy neighbour: p50={contended.p50:6.2f}s  p95={contended.p95:6.2f}s")
    print(f"  p95 degradation factor     : {degradation:.2f}x")
    return degradation


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--noisy-cases", type=int, default=60)
    parser.add_argument("--quiet-cases", type=int, default=10)
    parser.add_argument(
        "--max-concurrent-activities",
        type=int,
        default=5,
        help="Per-worker activity slots. Low on purpose: contention is the thing being measured.",
    )
    parser.add_argument("--noisy-tier", default="standard")
    parser.add_argument("--quiet-tier", default="enterprise")
    args = parser.parse_args()

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)

    print(
        f"noisy tenant: {args.noisy_cases} cases on '{args.noisy_tier}'\n"
        f"quiet tenant: {args.quiet_cases} cases on '{args.quiet_tier}'\n"
        f"activity duration: {_ACTIVITY_SECONDS}s, "
        f"{args.max_concurrent_activities} slots per worker"
    )

    results = {}
    for topology in ("shared", "per-tier"):
        baseline, contended = await _run_experiment(
            client,
            topology,
            args.noisy_cases,
            args.quiet_cases,
            args.max_concurrent_activities,
            args.noisy_tier,
            args.quiet_tier,
        )
        results[topology] = _report(topology, baseline, contended)

    print("\n=== finding ===")
    for topology, degradation in results.items():
        verdict = "isolated" if degradation < 2.0 else "DEGRADED"
        print(f"  {topology:9s}: {degradation:5.2f}x p95 -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
