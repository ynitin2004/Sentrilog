import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from services.intake import db
from services.intake.auth import hash_api_key
from services.intake.main import app

# Test-teardown-only: sentrilog_app (the app's runtime role) intentionally has no DELETE
# grant on any table -- an app that can create cases but never delete them is a deliberate
# part of the audit-integrity design, not an oversight. Cleaning up test data therefore needs
# the superuser role; production code must never use this connection string.
_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _db_pool() -> AsyncIterator[None]:
    await db.init_pool()
    yield
    await db.close_pool()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_tenant_and_key(name: str, slug: str) -> tuple[str, str]:
    """Creates a tenant + API key through the exact same RLS-governed path the app itself
    uses (set_config before writing to the RLS-protected api_keys table), rather than
    reaching for superuser credentials as a test-only shortcut.
    """
    pool = db.get_pool()
    async with pool.acquire() as conn, conn.transaction():
        tenant_id = await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id", name, slug
        )
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
        raw_key = secrets.token_urlsafe(24)
        await conn.execute(
            "INSERT INTO api_keys (tenant_id, key_hash, name) VALUES ($1, $2, $3)",
            tenant_id,
            hash_api_key(raw_key),
            "test-key",
        )
    return str(tenant_id), raw_key


async def _create_reviewer(
    tenant_id: str, email: str, *, role: str = "reviewer", revoked: bool = False
) -> tuple[str, str]:
    """Same RLS-governed pattern as _create_tenant_and_key, for reviewers.token_hash."""
    pool = db.get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
        raw_token = secrets.token_urlsafe(24)
        reviewer_id = await conn.fetchval(
            "INSERT INTO reviewers (tenant_id, email, role, token_hash, revoked_at) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            tenant_id,
            email,
            role,
            hash_api_key(raw_token),
            datetime.now(UTC) if revoked else None,
        )
    return str(reviewer_id), raw_token


async def _delete_tenant(tenant_id: str) -> None:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM review_decisions WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM webhook_deliveries WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM extractions WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM face_matches WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM sanctions_hits WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM documents WHERE tenant_id = $1", tenant_id)
            # cases.claimed_by_reviewer_id references reviewers(id) with no ON DELETE clause --
            # cases must go before reviewers, not just before tenants.
            await conn.execute("DELETE FROM cases WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM reviewers WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM webhooks WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM api_keys WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def two_tenants() -> AsyncIterator[dict[str, dict[str, str]]]:
    suffix = secrets.token_hex(4)
    tenant_a_id, key_a = await _create_tenant_and_key(f"Test A {suffix}", f"test-a-{suffix}")
    tenant_b_id, key_b = await _create_tenant_and_key(f"Test B {suffix}", f"test-b-{suffix}")
    try:
        yield {
            "a": {"tenant_id": tenant_a_id, "api_key": key_a},
            "b": {"tenant_id": tenant_b_id, "api_key": key_b},
        }
    finally:
        await _delete_tenant(tenant_a_id)
        await _delete_tenant(tenant_b_id)


@pytest_asyncio.fixture
async def two_tenants_with_reviewers() -> AsyncIterator[dict[str, dict[str, str]]]:
    suffix = secrets.token_hex(4)
    tenant_a_id, key_a = await _create_tenant_and_key(f"Test A {suffix}", f"test-a-{suffix}")
    tenant_b_id, key_b = await _create_tenant_and_key(f"Test B {suffix}", f"test-b-{suffix}")
    reviewer_a_id, token_a = await _create_reviewer(tenant_a_id, f"reviewer-a-{suffix}@test.local")
    reviewer_b_id, token_b = await _create_reviewer(tenant_b_id, f"reviewer-b-{suffix}@test.local")
    try:
        yield {
            "a": {
                "tenant_id": tenant_a_id,
                "api_key": key_a,
                "reviewer_id": reviewer_a_id,
                "reviewer_token": token_a,
            },
            "b": {
                "tenant_id": tenant_b_id,
                "api_key": key_b,
                "reviewer_id": reviewer_b_id,
                "reviewer_token": token_b,
            },
        }
    finally:
        await _delete_tenant(tenant_a_id)
        await _delete_tenant(tenant_b_id)


async def _insert_case(tenant_id: str, *, status: str, subject_name: str = "Jane Doe") -> str:
    pool = db.get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
        case_id = await conn.fetchval(
            "INSERT INTO cases (tenant_id, subject_name, status) VALUES ($1, $2, $3) "
            "RETURNING id",
            tenant_id,
            subject_name,
            status,
        )
    return str(case_id)
