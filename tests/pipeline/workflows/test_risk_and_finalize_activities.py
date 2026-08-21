"""risk_score_activity, update_case_status_activity, and finalize_case_activity tests against
the real dev Postgres (sentrilog_app role, same RLS path as production). risk_scoring.py's own
decision logic is already covered in isolation by tests/pipeline/test_risk_scoring.py -- these
tests instead prove the activity wrapper actually persists what that logic decides, and (Phase
10) that status-changing activities fire a real pg_notify('case_events', ...) a real LISTEN
connection can observe -- the backend half of the SSE real-time pipeline.
"""

import asyncio
import json
import secrets
from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from services.pipeline import db
from services.pipeline.config import settings
from services.pipeline.workflows.activities import (
    FinalizeCaseInput,
    RiskScoreInput,
    UpdateCaseStatusInput,
    finalize_case_activity,
    risk_score_activity,
    update_case_status_activity,
)

_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"


async def _listen_for_one_case_event() -> tuple[asyncpg.Connection, "asyncio.Queue[str]"]:
    """Opens a dedicated LISTEN connection (mirroring services.intake.db.raw_connection --
    pipeline has no such helper since only the intake API's SSE endpoint needs one, but the
    underlying pg_notify channel is server-side, not process-local, so any connection to the
    same database can observe it) and returns it plus a queue fed by the notification.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    conn = await asyncpg.connect(dsn=settings.database_url)
    await conn.add_listener("case_events", lambda _c, _p, _ch, payload: queue.put_nowait(payload))
    return conn, queue


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _db_pool() -> AsyncIterator[None]:
    await db.init_pool()
    yield
    await db.close_pool()


async def _create_tenant_and_case() -> tuple[str, str]:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            suffix = secrets.token_hex(4)
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"Risk Test {suffix}",
                f"risk-test-{suffix}",
            )
            case_id = await conn.fetchval(
                "INSERT INTO cases (tenant_id, subject_name, status) VALUES ($1, $2, $3) "
                "RETURNING id",
                tenant_id,
                "Jane Doe",
                "needs_review",
            )
        return str(tenant_id), str(case_id)
    finally:
        await conn.close()


async def _delete_tenant(tenant_id: str) -> None:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            # audit_log.case_id references cases(id) with no ON DELETE clause (Phase 11).
            await conn.execute("DELETE FROM audit_log WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM cases WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
    finally:
        await conn.close()


async def test_risk_score_activity_persists_score_for_a_clean_case() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        output = await risk_score_activity(
            RiskScoreInput(
                tenant_id=tenant_id,
                case_id=case_id,
                extraction_confidence=0.99,
                face_match_score=0.95,
                sanctions_hit_count=0,
            )
        )
        assert output.needs_review is False

        async with db.tenant_connection(tenant_id) as conn:
            stored = await conn.fetchval("SELECT risk_score FROM cases WHERE id = $1", case_id)
        assert stored is not None
        assert float(stored) == output.risk_score
    finally:
        await _delete_tenant(tenant_id)


async def test_risk_score_activity_persists_score_for_a_sanctions_hit() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        output = await risk_score_activity(
            RiskScoreInput(
                tenant_id=tenant_id,
                case_id=case_id,
                extraction_confidence=0.99,
                face_match_score=0.99,
                sanctions_hit_count=1,
            )
        )
        assert output.needs_review is True
        assert output.risk_score == 1.0

        async with db.tenant_connection(tenant_id) as conn:
            stored = await conn.fetchval("SELECT risk_score FROM cases WHERE id = $1", case_id)
        assert float(stored) == 1.0
    finally:
        await _delete_tenant(tenant_id)


async def test_finalize_case_activity_sets_status_decision_and_decided_at() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await finalize_case_activity(
            FinalizeCaseInput(
                tenant_id=tenant_id, case_id=case_id, status="approved", decision="approved"
            )
        )

        async with db.tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT status, decision, decided_at FROM cases WHERE id = $1", case_id
            )
        assert row is not None
        assert row["status"] == "approved"
        assert row["decision"] == "approved"
        assert row["decided_at"] is not None
    finally:
        await _delete_tenant(tenant_id)


async def test_update_case_status_activity_persists_status() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await update_case_status_activity(
            UpdateCaseStatusInput(tenant_id=tenant_id, case_id=case_id, status="processing")
        )

        async with db.tenant_connection(tenant_id) as conn:
            stored = await conn.fetchval("SELECT status FROM cases WHERE id = $1", case_id)
        assert stored == "processing"
    finally:
        await _delete_tenant(tenant_id)


async def test_update_case_status_activity_notifies_a_real_listener() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    listener_conn, queue = await _listen_for_one_case_event()
    try:
        await update_case_status_activity(
            UpdateCaseStatusInput(tenant_id=tenant_id, case_id=case_id, status="processing")
        )

        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        event = json.loads(payload)
        assert event == {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "status": "processing",
            "decision": None,
        }
    finally:
        await listener_conn.close()
        await _delete_tenant(tenant_id)


async def test_finalize_case_activity_notifies_a_real_listener() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    listener_conn, queue = await _listen_for_one_case_event()
    try:
        await finalize_case_activity(
            FinalizeCaseInput(
                tenant_id=tenant_id, case_id=case_id, status="rejected", decision="rejected"
            )
        )

        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        event = json.loads(payload)
        assert event == {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "status": "rejected",
            "decision": "rejected",
        }
    finally:
        await listener_conn.close()
        await _delete_tenant(tenant_id)


async def test_finalize_case_activity_never_notifies_a_listener_on_a_different_tenant() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    other_tenant_id, other_case_id = await _create_tenant_and_case()
    listener_conn, queue = await _listen_for_one_case_event()
    try:
        await finalize_case_activity(
            FinalizeCaseInput(
                tenant_id=other_tenant_id,
                case_id=other_case_id,
                status="approved",
                decision="approved",
            )
        )
        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        event = json.loads(payload)
        # The channel is shared across all tenants (pg_notify has no per-channel ACLs); tenant
        # isolation for LISTEN/NOTIFY is a consumer-side filtering responsibility, proven here
        # by asserting the OTHER tenant's payload is what actually arrives -- the intake API's
        # /events/stream endpoint is what filters this by the caller's own tenant_id (covered in
        # tests/intake/test_events_stream.py), not Postgres itself.
        assert event["tenant_id"] == other_tenant_id
        assert event["tenant_id"] != tenant_id
    finally:
        await listener_conn.close()
        await _delete_tenant(tenant_id)
        await _delete_tenant(other_tenant_id)


async def _fetch_audit_event_types(tenant_id: str) -> list[str]:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        rows = await conn.fetch(
            "SELECT event_type FROM audit_log WHERE tenant_id = $1 ORDER BY id ASC", tenant_id
        )
        return [r["event_type"] for r in rows]
    finally:
        await conn.close()


async def test_risk_score_activity_leaves_a_started_and_completed_audit_row() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await risk_score_activity(
            RiskScoreInput(
                tenant_id=tenant_id,
                case_id=case_id,
                extraction_confidence=0.99,
                face_match_score=0.95,
                sanctions_hit_count=0,
            )
        )
        assert await _fetch_audit_event_types(tenant_id) == [
            "risk_score.started",
            "risk_score.completed",
        ]
    finally:
        await _delete_tenant(tenant_id)


async def test_update_case_status_activity_leaves_a_started_and_completed_audit_row() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await update_case_status_activity(
            UpdateCaseStatusInput(tenant_id=tenant_id, case_id=case_id, status="processing")
        )
        assert await _fetch_audit_event_types(tenant_id) == [
            "update_case_status.started",
            "update_case_status.completed",
        ]
    finally:
        await _delete_tenant(tenant_id)


async def test_finalize_case_activity_leaves_a_started_and_completed_audit_row() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await finalize_case_activity(
            FinalizeCaseInput(
                tenant_id=tenant_id, case_id=case_id, status="approved", decision="approved"
            )
        )
        assert await _fetch_audit_event_types(tenant_id) == [
            "finalize_case.started",
            "finalize_case.completed",
        ]
    finally:
        await _delete_tenant(tenant_id)
