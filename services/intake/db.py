from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=10)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized -- call init_pool() first")
    return _pool


async def resolve_api_key(key_hash: str) -> asyncpg.Record | None:
    """Looks up an API key's tenant via the SECURITY DEFINER function, bypassing RLS --
    this is the one query that must run before a tenant is known (see 003_auth_lookup.sql).
    """
    pool = get_pool()
    return await pool.fetchrow(
        "SELECT tenant_id, api_key_id, revoked_at FROM resolve_api_key($1)", key_hash
    )


async def resolve_reviewer_token(token_hash: str) -> asyncpg.Record | None:
    """Same pattern as resolve_api_key, for reviewer bearer tokens
    (see 006_reviewer_auth_and_claims.sql)."""
    pool = get_pool()
    return await pool.fetchrow(
        "SELECT tenant_id, reviewer_id, role, revoked_at FROM resolve_reviewer_token($1)",
        token_hash,
    )


async def raw_connection() -> asyncpg.Connection:
    """Opens a dedicated, non-pooled connection.

    LISTEN needs a connection held open for the lifetime of an SSE stream -- a pooled
    connection from tenant_connection() gets released back to the pool (and its LISTEN state
    reset) as soon as that async-with block exits, which is wrong for something long-lived.
    """
    return await asyncpg.connect(dsn=settings.database_url)


@asynccontextmanager
async def tenant_connection(tenant_id: str) -> AsyncIterator[asyncpg.Connection]:
    """Acquires a pooled connection scoped to one tenant for a single transaction.

    SET LOCAL (not SET) is essential: it resets automatically at transaction end, so a
    tenant_id can never leak onto a connection the pool later hands to a different tenant's
    request.
    """
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # SET/SET LOCAL do not accept bind parameters in Postgres -- only set_config() does,
        # via its third ('is_local') argument, which is the parameterized equivalent of
        # SET LOCAL. Using string-formatted SET here would reopen a SQL-injection path on
        # tenant_id, which is exactly the value RLS depends on to keep tenants apart.
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
        yield conn
