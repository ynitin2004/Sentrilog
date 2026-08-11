"""One-off manual script proving Phase 5's exit criteria for real: kill the worker mid-run,
restart it, confirm the workflow resumes without losing progress. Not part of the automated
suite -- it deliberately starts/kills real OS processes, which pytest isn't the right tool for.

Registers a deliberately slow fake fetch_id_document_activity so there's a wide, reliable
window to kill this process mid-activity from another script -- the real extraction activity's
actual latency (sub-second to a few seconds against Gemini) would make "kill it mid-run" an
unreliable thing to script deterministically.

Usage: uv run python scripts/resilience_test_worker.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from temporalio import activity  # noqa: E402
from temporalio.client import Client  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from services.pipeline.config import settings  # noqa: E402
from services.pipeline.workflows.activities import (  # noqa: E402
    DocumentRef,
    FetchDocumentInput,
    UpdateCaseStatusInput,
)
from services.pipeline.workflows.kyc_case import KycCaseWorkflow  # noqa: E402

TASK_QUEUE = "resilience-test-queue"
SLEEP_SECONDS = int(os.environ.get("RESILIENCE_TEST_SLEEP", "15"))


@activity.defn(name="fetch_id_document_activity")
async def slow_fake_fetch(input: FetchDocumentInput) -> DocumentRef | None:
    print(f"[pid={os.getpid()}] fetch activity STARTED, sleeping {SLEEP_SECONDS}s...", flush=True)
    await asyncio.sleep(SLEEP_SECONDS)
    print(f"[pid={os.getpid()}] fetch activity COMPLETED", flush=True)
    return None  # short-circuits the workflow to needs_review -- surviving the sleep is the point


@activity.defn(name="update_case_status_activity")
async def fake_update_status(input: UpdateCaseStatusInput) -> None:
    print(f"[pid={os.getpid()}] status -> {input.status}", flush=True)


async def main() -> None:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[KycCaseWorkflow],
        activities=[slow_fake_fetch, fake_update_status],
    )
    print(f"[pid={os.getpid()}] worker started, polling {TASK_QUEUE}", flush=True)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
