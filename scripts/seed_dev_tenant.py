"""Bootstraps a tenant + API key for local dev/testing. There's no admin API yet (deliberately
out of scope for now, per PLAN.md §7), so this is the only way to get a usable API key locally.

Usage: uv run python scripts/seed_dev_tenant.py [tenant-name] [tenant-slug]
Prints the raw API key once -- it is never stored or recoverable after this.
"""

import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from services.intake.auth import hash_api_key  # noqa: E402
from services.intake.config import settings  # noqa: E402


async def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "Dev Tenant"
    slug = sys.argv[2] if len(sys.argv) > 2 else "dev-tenant"

    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        # api_keys has row-level security requiring app.tenant_id to match the row being
        # written (002_multi_tenancy.sql) -- even this bootstrap script has to set it before
        # inserting the key, same as any normal request would. One transaction so both writes
        # commit (or roll back) together, and SET LOCAL only needs setting once for both.
        async with conn.transaction():
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) "
                "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name "
                "RETURNING id",
                name,
                slug,
            )
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
            raw_key = secrets.token_urlsafe(32)
            await conn.execute(
                "INSERT INTO api_keys (tenant_id, key_hash, name) VALUES ($1, $2, $3)",
                tenant_id,
                hash_api_key(raw_key),
                "dev-seed-key",
            )
    finally:
        await conn.close()

    print(f"tenant_id: {tenant_id}")
    print(f"api_key:   {raw_key}")


if __name__ == "__main__":
    asyncio.run(main())
