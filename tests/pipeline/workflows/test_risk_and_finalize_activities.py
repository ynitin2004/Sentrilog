"""risk_score_activity and finalize_case_activity tests against the real dev Postgres
(sentrilog_app role, same RLS path as production). risk_scoring.py's own decision logic is
already covered in isolation by tests/pipeline/test_risk_scoring.py -- these tests instead
prove the activity wrapper actually persists what that logic decides.
"""

import secrets
from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from services.pipeline import db
from services.pipeline.workflows.activities import (
    FinalizeCaseInput,
    RiskScoreInput,
    finalize_case_activity,
    risk_score_activity,
)

_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"


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
