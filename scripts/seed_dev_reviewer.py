"""Bootstraps a reviewer for local dev/testing against an existing tenant. There's no admin API
yet (deliberately out of scope for now, per PLAN.md §7), so this is the only way to get a usable
reviewer token locally, mirroring seed_dev_tenant.py's pattern for API keys.

Usage: uv run python scripts/seed_dev_reviewer.py <tenant-id> [email] [role]
Prints the raw reviewer token once -- it is never stored or recoverable after this.
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
    if len(sys.argv) < 2:
        print("usage: uv run python scripts/seed_dev_reviewer.py <tenant-id> [email] [role]")
        raise SystemExit(1)

    tenant_id = sys.argv[1]
    email = sys.argv[2] if len(sys.argv) > 2 else "reviewer@dev.local"
    role = sys.argv[3] if len(sys.argv) > 3 else "reviewer"

    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            raw_token = secrets.token_urlsafe(32)
            reviewer_id = await conn.fetchval(
                "INSERT INTO reviewers (tenant_id, email, role, token_hash) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (tenant_id, email) DO UPDATE SET token_hash = EXCLUDED.token_hash "
                "RETURNING id",
                tenant_id,
                email,
                role,
                hash_api_key(raw_token),
            )
    finally:
        await conn.close()

    print(f"reviewer_id:    {reviewer_id}")
    print(f"reviewer_token: {raw_token}")


if __name__ == "__main__":
    asyncio.run(main())
