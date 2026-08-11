"""Worker process entrypoint: uv run python -m services.pipeline.workflows.worker

Polls all plan-tier task queues concurrently (see task_queues.py) -- a real deployment would
run a separately-sized worker pool per queue so one tenant's backlog can't starve another's,
but proving *that* is Phase 9's load-testing job. This proves the routing mechanism itself
works: three real Temporal Worker instances, three real task queues.
"""

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from .. import db
from ..config import settings
from .activities import (
    extract_document_activity,
    fetch_id_document_activity,
    update_case_status_activity,
)
from .kyc_case import KycCaseWorkflow
from .task_queues import PLAN_TIERS, task_queue_for_plan_tier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    await db.init_pool()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)

    workers = [
        Worker(
            client,
            task_queue=task_queue_for_plan_tier(tier),
            workflows=[KycCaseWorkflow],
            activities=[
                fetch_id_document_activity,
                extract_document_activity,
                update_case_status_activity,
            ],
        )
        for tier in PLAN_TIERS
    ]

    logger.info(
        "starting worker, polling task queues: %s",
        [task_queue_for_plan_tier(t) for t in PLAN_TIERS],
    )
    try:
        await asyncio.gather(*(w.run() for w in workers))
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
