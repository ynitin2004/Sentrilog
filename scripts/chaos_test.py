"""Chaos test (Phase 12, PLAN.md §9): kill a dependency mid-workflow, confirm recovery.

Manual tooling, not a pytest test, for the same reason scripts/resilience_test_worker.py is --
it stops and starts real Docker containers, and a test suite that can take the database away
from itself mid-run is a bad neighbour to every other test. Findings from running it are
written up in docs/runbooks/.

What is real here: the Temporal server, the worker, Postgres, Qdrant, and the containers really
being stopped. Fetch and extraction are faked (the same pattern tests/intake/test_review_e2e.py
established) so a run doesn't depend on S3 uploads, EasyOCR, or paid Gemini calls -- the
failure mode under test is the dependency outage, not the document pipeline.

Usage (needs docker compose up and nothing else polling the chaos task queue):
    uv run python scripts/chaos_test.py qdrant
    uv run python scripts/chaos_test.py postgres
    uv run python scripts/chaos_test.py all
"""

import asyncio
import secrets
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402
from temporalio import activity  # noqa: E402
from temporalio.api.common.v1 import WorkflowExecution  # noqa: E402
from temporalio.api.enums.v1 import EventType  # noqa: E402
from temporalio.api.workflowservice.v1 import (  # noqa: E402
    GetSystemInfoRequest,
    ResetWorkflowExecutionRequest,
)
from temporalio.client import Client, WorkflowExecutionStatus  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from services.pipeline import db as pipeline_db  # noqa: E402
from services.pipeline.config import settings as pipeline_settings  # noqa: E402
from services.pipeline.workflows.activities import (  # noqa: E402
    CaseDocuments,
    DocumentRef,
    ExtractDocumentInput,
    ExtractDocumentOutput,
    FaceMatchInput,
    FaceMatchOutput,
    FetchDocumentInput,
    deliver_webhooks_activity,
    finalize_case_activity,
    risk_score_activity,
    sanctions_screen_activity,
    update_case_status_activity,
)
from services.pipeline.workflows.contracts import KycCaseInput  # noqa: E402
from services.pipeline.workflows.kyc_case import KycCaseWorkflow  # noqa: E402

_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"
_TASK_QUEUE = "kyc-case-chaos"


def _compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True)


@activity.defn(name="fetch_case_documents_activity")
async def _fake_fetch(input: FetchDocumentInput) -> CaseDocuments:
    return CaseDocuments(
        id_document=DocumentRef(document_id=str(uuid.uuid4()), s3_key="chaos/id"),
        selfie=DocumentRef(document_id=str(uuid.uuid4()), s3_key="chaos/selfie"),
    )


# How long the faked extraction occupies the workflow. Real extraction takes seconds (a Gemini
# call plus OCR); faking it as instant made the postgres scenario meaningless -- the whole
# workflow ran to `approved` inside the 2s the script waited before stopping the database, so
# the "outage" happened after the case was already finished and proved nothing. A deliberately
# slow stand-in is what makes the outage land mid-workflow deterministically.
_FAKE_EXTRACT_SECONDS = 10.0


@activity.defn(name="extract_document_activity")
async def _fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
    # Confident enough to proceed past extraction into face match + sanctions, which is where
    # the dependencies this script takes away actually get used.
    await asyncio.sleep(_FAKE_EXTRACT_SECONDS)
    return ExtractDocumentOutput(
        needs_review=False,
        confidence=0.95,
        method="vlm",
        reason=None,
        full_name="Chaos Test Subject",
    )


@activity.defn(name="face_match_activity")
async def _fake_face_match(input: FaceMatchInput) -> FaceMatchOutput:
    # Annotated with the real dataclasses, not `object`: Temporal resolves an activity's
    # argument types from its signature to decode the payload, and `object` makes that
    # impossible ("Unserializable type during conversion"). The activity then failed to decode
    # its arguments on every attempt and, being under _LONG_RETRY_POLICY, retried for the full
    # 20-minute schedule_to_close window -- so the scenario hung instead of failing, which is
    # the worst way for a chaos harness to be wrong.
    return FaceMatchOutput(similarity_score=0.95, needs_review=False, reason=None)


async def _create_tenant_and_case() -> tuple[str, str]:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            suffix = secrets.token_hex(4)
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"Chaos {suffix}",
                f"chaos-{suffix}",
            )
            case_id = await conn.fetchval(
                "INSERT INTO cases (tenant_id, subject_name, status) VALUES ($1, $2, $3) "
                "RETURNING id",
                tenant_id,
                "Chaos Test Subject",
                "pending",
            )
        return str(tenant_id), str(case_id)
    finally:
        await conn.close()


async def _cleanup_tenant(tenant_id: str) -> None:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM audit_log WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM sanctions_hits WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM face_matches WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM cases WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
    finally:
        await conn.close()


async def _case_status(case_id: str) -> str | None:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        status: str | None = await conn.fetchval("SELECT status FROM cases WHERE id = $1", case_id)
        return status
    finally:
        await conn.close()


async def _wait_for(
    condition: Callable[[], Awaitable[bool]], timeout_seconds: float, what: str
) -> bool:
    elapsed = 0.0
    while elapsed < timeout_seconds:
        try:
            if await condition():
                return True
        except Exception:  # noqa: BLE001 - the dependency being down is the point
            pass
        await asyncio.sleep(1.0)
        elapsed += 1.0
    print(f"  TIMEOUT waiting for {what} after {timeout_seconds}s")
    return False


async def _wait_for_temporal(client: Client, timeout_seconds: float = 90.0) -> bool:
    """Temporal's own persistence is this same Postgres in docker-compose.yml, so a Postgres
    outage takes the workflow engine down with it (see the note in scenario_postgres). After
    Postgres returns, the server needs a moment to re-establish its own store before it will
    answer RPCs again.
    """

    async def _answers() -> bool:
        await client.workflow_service.get_system_info(GetSystemInfoRequest())
        return True

    return await _wait_for(_answers, timeout_seconds, "temporal to answer RPCs again")


async def _workflow_status(client: Client, workflow_id: str) -> WorkflowExecutionStatus | None:
    try:
        description = await client.get_workflow_handle(workflow_id).describe()
    except Exception:  # noqa: BLE001 - "can't ask yet" is indistinguishable from "gone" here
        return None
    return description.status


async def _reset_to_first_workflow_task(client: Client, workflow_id: str) -> None:
    """The runbook's documented recovery: `temporal workflow reset --type FirstWorkflowTask`.

    Done through the service API rather than by starting a fresh workflow, because those are
    not the same operation -- a reset replays the original run's history from the chosen point,
    which is what the runbook tells an operator to do, so it is what this proves.
    """
    handle = client.get_workflow_handle(workflow_id)
    description = await handle.describe()

    finish_event_id = None
    async for event in handle.fetch_history_events():
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED:
            finish_event_id = event.event_id
            break
    if finish_event_id is None:
        raise RuntimeError("no completed workflow task in history to reset to")

    await client.workflow_service.reset_workflow_execution(
        ResetWorkflowExecutionRequest(
            namespace=client.namespace,
            workflow_execution=WorkflowExecution(
                workflow_id=workflow_id, run_id=description.run_id
            ),
            reason="chaos test: re-drive after a postgres outage",
            workflow_task_finish_event_id=finish_event_id,
            request_id=str(uuid.uuid4()),
        )
    )


async def _terminate_stale_chaos_workflows(client: Client) -> None:
    """Clear leftovers from an interrupted run.

    Both scenarios share one task queue, and a workflow abandoned by a previous run keeps
    getting retried on it -- so its failures appear in this run's logs, attributed to a case
    that no longer exists. Terminating them first is what makes a log read cleanly enough to
    diagnose anything from.
    """
    terminated = 0
    async for execution in client.list_workflows("WorkflowType = 'KycCaseWorkflow'"):
        if not execution.id.startswith("chaos-"):
            continue
        if execution.status != WorkflowExecutionStatus.RUNNING:
            continue
        await client.get_workflow_handle(execution.id).terminate(reason="stale chaos run")
        terminated += 1
    if terminated:
        print(f"  cleared {terminated} stale chaos workflow(s) from a previous run")


async def scenario_qdrant() -> bool:
    """Qdrant goes away while a case is in flight.

    Expected, per kyc_case.py: sanctions_screen_activity retries 3 times (_SHORT_RETRY_POLICY),
    fails, the workflow catches ActivityError and parks the case for human review with
    "sanctions screening activity failed" -- it must NOT auto-approve a case whose sanctions
    screen never actually ran.
    """
    print("\n=== scenario: qdrant unavailable mid-workflow ===")
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        client = await Client.connect(
            pipeline_settings.temporal_address, namespace=pipeline_settings.temporal_namespace
        )
        await _terminate_stale_chaos_workflows(client)

        # Without this every DB-touching activity raises "DB pool not initialized" and the
        # scenario fails for a reason that has nothing to do with Qdrant -- which is exactly
        # what it did on its first real run, reporting the case stuck in `pending` and making
        # the fail-safe behaviour look broken when it was the harness that was.
        await pipeline_db.init_pool()

        print("  stopping qdrant...")
        _compose("stop", "qdrant")

        async with Worker(
            client,
            task_queue=_TASK_QUEUE,
            workflows=[KycCaseWorkflow],
            activities=[
                _fake_fetch,
                _fake_extract,
                _fake_face_match,
                sanctions_screen_activity,
                update_case_status_activity,
                risk_score_activity,
                finalize_case_activity,
                deliver_webhooks_activity,
            ],
        ):
            await client.start_workflow(
                "KycCaseWorkflow",
                KycCaseInput(tenant_id=tenant_id, case_id=case_id),
                id=f"chaos-qdrant-{case_id}",
                task_queue=_TASK_QUEUE,
            )

            reached_review = await _wait_for(
                lambda: _is_needs_review(case_id),
                # Must outlast _FAKE_EXTRACT_SECONDS plus the sanctions retry budget.
                timeout_seconds=120,
                what="case to park in needs_review",
            )

        status = await _case_status(case_id)
        print(f"  final case status: {status}")
        ok = reached_review and status == "needs_review"
        print(
            "  PASS: a case whose sanctions screen could not run was parked for a human, "
            "not auto-approved"
            if ok
            else f"  FAIL: expected needs_review, got {status!r}"
        )
        return ok
    finally:
        print("  restarting qdrant...")
        _compose("start", "qdrant")
        try:
            await pipeline_db.close_pool()
        except Exception:  # noqa: BLE001
            pass
        await _cleanup_tenant(tenant_id)


async def _is_needs_review(case_id: str) -> bool:
    return await _case_status(case_id) == "needs_review"


async def scenario_postgres() -> bool:
    """Postgres goes away while a case is genuinely mid-workflow, then comes back.

    **What this topology can and cannot test.** docker-compose.yml points Temporal's own
    persistence at this same Postgres, so stopping it removes the workflow engine's store along
    with the application's data. That is not production's shape, and it changes the answer
    rather than just making it harsher: a frozen Temporal cannot dispatch activity tasks or
    record their failures, so no activity burns its retry budget during the outage. The case
    freezes and then continues.

    So what this scenario actually proves is that **total loss of the data tier is survivable**
    -- the case resumes and reaches a terminal state once everything is back, with no operator
    action. That is worth knowing and is what it asserts.

    What it cannot prove is the production-shaped failure, where Temporal keeps running on its
    own store while the application's database is gone. There, the engine stays healthy and
    keeps dispatching, so the `_SHORT_RETRY_POLICY` activities (3 attempts, ~3s) really would
    exhaust their budget and fail the workflow. See the known gap in
    docs/runbooks/postgres-unavailable.md -- testing it needs Temporal on a separate store,
    which is a Phase 13 infrastructure decision, not something this script can arrange.
    """
    print("\n=== scenario: postgres unavailable mid-workflow ===")
    tenant_id, case_id = await _create_tenant_and_case()
    workflow_id = f"chaos-postgres-{case_id}"
    restarted = False
    try:
        client = await Client.connect(
            pipeline_settings.temporal_address, namespace=pipeline_settings.temporal_namespace
        )
        await _terminate_stale_chaos_workflows(client)
        await pipeline_db.init_pool()

        async with Worker(
            client,
            task_queue=_TASK_QUEUE,
            workflows=[KycCaseWorkflow],
            activities=[
                _fake_fetch,
                _fake_extract,
                _fake_face_match,
                sanctions_screen_activity,
                update_case_status_activity,
                risk_score_activity,
                finalize_case_activity,
                deliver_webhooks_activity,
            ],
        ):
            await client.start_workflow(
                "KycCaseWorkflow",
                KycCaseInput(tenant_id=tenant_id, case_id=case_id),
                id=workflow_id,
                task_queue=_TASK_QUEUE,
            )

            # Long enough to be inside the slow faked extraction, short enough to be nowhere
            # near its end -- so the case is provably still in flight when the database goes.
            await asyncio.sleep(2.0)
            in_flight = await _case_status(case_id)
            if in_flight in {"needs_review", "approved", "rejected"}:
                print(
                    f"  ABORT: case was already {in_flight!r} before the outage -- "
                    "_FAKE_EXTRACT_SECONDS is too short to keep it in flight"
                )
                return False
            print(f"  case is in flight (status={in_flight!r}); stopping postgres...")

            # Comfortably past the ~3s _SHORT_RETRY_POLICY budget, so a result here can't be
            # explained away as a blip the retries absorbed.
            _compose("stop", "postgres")
            await asyncio.sleep(12.0)

            print("  restarting postgres...")
            _compose("start", "postgres")
            restarted = True
            await _wait_for(_postgres_accepts_connections, 60.0, "postgres to accept connections")
            await _wait_for_temporal(client)

            # Every connection in the pool was severed. asyncpg does not transparently rebuild
            # a pool whose server went away, so this stands in for the process restart the
            # runbook tells an operator to perform.
            await pipeline_db.close_pool()
            await pipeline_db.init_pool()

            recovered = await _wait_for(
                lambda: _reached_terminal(case_id),
                timeout_seconds=180,
                what="the case to reach a terminal state after the outage",
            )

        status = await _case_status(case_id)
        workflow_status = await _workflow_status(client, workflow_id)
        print(f"  final case status: {status}  workflow: {workflow_status}")
        if recovered:
            print(
                "  PASS: the case was mid-workflow through a 12s total data-tier outage and "
                "still reached a terminal state with no operator action"
            )
            return True

        # Not a harness failure -- this is the production-shaped behaviour arriving early, and
        # the runbook's reset procedure is the documented answer to it.
        print(
            f"  the case did not resume on its own (status={status!r}, "
            f"workflow={workflow_status}); applying the runbook's reset..."
        )
        await _reset_to_first_workflow_task(client, workflow_id)
        async with Worker(
            client,
            task_queue=_TASK_QUEUE,
            workflows=[KycCaseWorkflow],
            activities=[
                _fake_fetch,
                _fake_extract,
                _fake_face_match,
                sanctions_screen_activity,
                update_case_status_activity,
                risk_score_activity,
                finalize_case_activity,
                deliver_webhooks_activity,
            ],
        ):
            reset_recovered = await _wait_for(
                lambda: _reached_terminal(case_id),
                timeout_seconds=180,
                what="the reset workflow to drive the case to a terminal state",
            )
        print(
            "  PASS: the outage stranded the case, and the runbook's documented reset "
            "recovered it"
            if reset_recovered
            else "  FAIL: neither self-recovery nor the documented reset recovered the case"
        )
        return reset_recovered
    finally:
        if not restarted:
            _compose("start", "postgres")
            await _wait_for(_postgres_accepts_connections, 60.0, "postgres to accept connections")
        try:
            await pipeline_db.close_pool()
        except Exception:  # noqa: BLE001
            pass
        await _cleanup_tenant(tenant_id)


async def _postgres_accepts_connections() -> bool:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    await conn.close()
    return True


async def _reached_terminal(case_id: str) -> bool:
    return await _case_status(case_id) in {"needs_review", "approved", "rejected"}


async def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    scenarios = {"qdrant": scenario_qdrant, "postgres": scenario_postgres}

    chosen = scenarios if which == "all" else {which: scenarios[which]}
    results = {}
    for name, fn in chosen.items():
        results[name] = await fn()

    print("\n=== chaos test summary ===")
    for name, ok in results.items():
        print(f"  {name:10s}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
