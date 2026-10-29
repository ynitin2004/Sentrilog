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
from temporalio.client import Client  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from services.pipeline import db as pipeline_db  # noqa: E402
from services.pipeline.config import settings as pipeline_settings  # noqa: E402
from services.pipeline.workflows.activities import (  # noqa: E402
    CaseDocuments,
    DocumentRef,
    ExtractDocumentInput,
    ExtractDocumentOutput,
    FetchDocumentInput,
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


@activity.defn(name="extract_document_activity")
async def _fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
    # Confident enough to proceed past extraction into face match + sanctions, which is where
    # the dependencies this script takes away actually get used.
    return ExtractDocumentOutput(
        needs_review=False,
        confidence=0.95,
        method="vlm",
        reason=None,
        full_name="Chaos Test Subject",
    )


@activity.defn(name="face_match_activity")
async def _fake_face_match(input: object) -> object:
    from services.pipeline.workflows.activities import FaceMatchOutput

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
        print("  stopping qdrant...")
        _compose("stop", "qdrant")

        client = await Client.connect(
            pipeline_settings.temporal_address, namespace=pipeline_settings.temporal_namespace
        )
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
                timeout_seconds=90,
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
        await _cleanup_tenant(tenant_id)


async def _is_needs_review(case_id: str) -> bool:
    return await _case_status(case_id) == "needs_review"


async def scenario_postgres() -> bool:
    """Postgres goes away mid-workflow and comes back.

    Expected: activities that write the database fail while it's gone; Temporal retries them,
    and once Postgres returns the workflow carries on from where it stopped rather than losing
    the case. What's being proven is that the case reaches a terminal state at all -- Temporal's
    durable timer/retry machinery is the safety net, since no application code retries.
    """
    print("\n=== scenario: postgres unavailable mid-workflow ===")
    tenant_id, case_id = await _create_tenant_and_case()
    restarted = False
    try:
        client = await Client.connect(
            pipeline_settings.temporal_address, namespace=pipeline_settings.temporal_namespace
        )
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
            ],
        ):
            await client.start_workflow(
                "KycCaseWorkflow",
                KycCaseInput(tenant_id=tenant_id, case_id=case_id),
                id=f"chaos-postgres-{case_id}",
                task_queue=_TASK_QUEUE,
            )
            await asyncio.sleep(2.0)

            print("  stopping postgres mid-flight...")
            _compose("stop", "postgres")
            await asyncio.sleep(10.0)

            print("  restarting postgres...")
            _compose("start", "postgres")
            restarted = True
            await asyncio.sleep(5.0)

            # The pool's connections were all severed; a fresh pool stands in for the restart
            # an operator would perform (see the runbook -- asyncpg does not transparently
            # re-establish a pool whose server went away).
            await pipeline_db.close_pool()
            await pipeline_db.init_pool()

            reached_terminal = await _wait_for(
                lambda: _reached_terminal(case_id),
                timeout_seconds=180,
                what="case to reach a terminal state after postgres returned",
            )

        status = await _case_status(case_id)
        print(f"  final case status: {status}")
        print(
            "  PASS: the case still reached a terminal state after the outage"
            if reached_terminal
            else "  FAIL: case never reached a terminal state"
        )
        return reached_terminal
    finally:
        if not restarted:
            _compose("start", "postgres")
            await asyncio.sleep(5.0)
        try:
            await pipeline_db.close_pool()
        except Exception:  # noqa: BLE001
            pass
        await _cleanup_tenant(tenant_id)


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
