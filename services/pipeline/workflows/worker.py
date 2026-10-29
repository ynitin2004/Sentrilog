"""Worker process entrypoint: uv run python -m services.pipeline.workflows.worker

Polls all plan-tier task queues concurrently (see task_queues.py) by default. A real deployment
runs a separately-sized worker pool per queue so one tenant's backlog can't starve another's --
Phase 12's noisy-neighbor load test measured exactly how much isolation this single-process
topology does and doesn't give (see PLAN.md's Phase 12 changelog). `--tier` runs one queue only,
which is what makes running real per-tier pools (and testing them) possible:

    uv run python -m services.pipeline.workflows.worker --tier standard
"""

import argparse
import asyncio

import structlog
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from services.common.observability import configure_logging, configure_tracing

from .. import db
from ..config import settings
from .activities import (
    deliver_webhooks_activity,
    extract_document_activity,
    face_match_activity,
    fetch_case_documents_activity,
    finalize_case_activity,
    risk_score_activity,
    sanctions_screen_activity,
    update_case_status_activity,
)
from .kyc_case import KycCaseWorkflow
from .task_queues import PLAN_TIERS, task_queue_for_plan_tier

configure_logging("pipeline-worker")
configure_tracing("pipeline-worker")
log = structlog.get_logger()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sentrilog pipeline worker")
    parser.add_argument(
        "--tier",
        choices=PLAN_TIERS,
        action="append",
        help=(
            "Poll only this plan tier's task queue (repeatable). Defaults to all tiers. "
            "Running one process per tier is what gives each tier independently-sized "
            "capacity -- see the noisy-neighbor findings in PLAN.md's Phase 12 changelog."
        ),
    )
    parser.add_argument(
        "--max-concurrent-activities",
        type=int,
        default=None,
        help=(
            "Cap concurrently-running activities for every worker this process starts. "
            "Unset means the Temporal SDK default; a real per-tier deployment sets this per "
            "pool so a burst on one tier can't consume capacity another tier is relying on."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    tiers: tuple[str, ...] = tuple(args.tier) if args.tier else PLAN_TIERS

    await db.init_pool()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=[TracingInterceptor()],
    )

    worker_kwargs = {}
    if args.max_concurrent_activities is not None:
        worker_kwargs["max_concurrent_activities"] = args.max_concurrent_activities

    workers = [
        Worker(
            client,
            task_queue=task_queue_for_plan_tier(tier),
            workflows=[KycCaseWorkflow],
            activities=[
                fetch_case_documents_activity,
                extract_document_activity,
                face_match_activity,
                sanctions_screen_activity,
                update_case_status_activity,
                risk_score_activity,
                finalize_case_activity,
                deliver_webhooks_activity,
            ],
            interceptors=[TracingInterceptor()],
            **worker_kwargs,
        )
        for tier in tiers
    ]

    log.info(
        "worker_starting",
        task_queues=[task_queue_for_plan_tier(t) for t in tiers],
        max_concurrent_activities=args.max_concurrent_activities,
    )
    try:
        await asyncio.gather(*(w.run() for w in workers))
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
