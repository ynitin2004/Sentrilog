"""Seeds one tenant, API key, reviewer and a real case parked for review, for the Playwright
flow in frontend/e2e/ (roadmap Phase 13c).

The case is parked by the real workflow, not by writing status = 'needs_review' into the
table: a case created with no documents makes KycCaseWorkflow park itself for review and wait
for a decision signal (services/pipeline/workflows/kyc_case.py). So the decision the browser
submits travels the production path -- API, Temporal signal, finalize activity -- and the test
can check the workflow completed, not just that a row changed.

Requires Postgres, Temporal and a running worker. Writes a JSON fixture for Playwright:

    uv run python scripts/seed_e2e.py frontend/e2e/.fixture.json
    uv run python scripts/seed_e2e.py --cleanup frontend/e2e/.fixture.json
"""

import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from services.intake.auth import hash_api_key  # noqa: E402
from services.intake.config import settings  # noqa: E402
from services.intake.temporal import start_kyc_case_workflow  # noqa: E402

# Teardown only, like the test suites' cleanup: sentrilog_app has no DELETE grant by design.
_ADMIN_DSN = os.environ.get(
    "SENTRILOG_ADMIN_DSN", "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"
)
_PARK_TIMEOUT_SECONDS = 90


async def seed(fixture_path: Path) -> None:
    suffix = secrets.token_hex(4)
    raw_key, raw_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    subject = f"E2E Subject {suffix}"

    conn = await asyncpg.connect(dsn=settings.database_url.get_secret_value())
    try:
        async with conn.transaction():
            tenant_id = str(
                await conn.fetchval(
                    "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                    f"E2E {suffix}",
                    f"e2e-{suffix}",
                )
            )
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await conn.execute(
                "INSERT INTO api_keys (tenant_id, key_hash, name) VALUES ($1, $2, 'e2e')",
                tenant_id,
                hash_api_key(raw_key),
            )
            await conn.execute(
                "INSERT INTO reviewers (tenant_id, email, role, token_hash) "
                "VALUES ($1, $2, 'reviewer', $3)",
                tenant_id,
                f"reviewer-{suffix}@e2e.local",
                hash_api_key(raw_token),
            )
            case_id = str(
                await conn.fetchval(
                    "INSERT INTO cases (tenant_id, subject_name) VALUES ($1, $2) RETURNING id",
                    tenant_id,
                    subject,
                )
            )
    finally:
        await conn.close()

    await start_kyc_case_workflow(tenant_id=tenant_id, case_id=case_id, plan_tier="standard")

    # Wait for the worker to park it: the queue is `status = 'needs_review'`.
    deadline = time.monotonic() + _PARK_TIMEOUT_SECONDS
    admin = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        while True:
            status = await admin.fetchval("SELECT status FROM cases WHERE id = $1", case_id)
            if status == "needs_review":
                break
            if time.monotonic() > deadline:
                raise SystemExit(f"case {case_id} never reached needs_review (is a worker up?)")
            await asyncio.sleep(1)
    finally:
        await admin.close()

    fixture_path.write_text(
        json.dumps(
            {
                "tenant_id": tenant_id,
                "case_id": case_id,
                "subject_name": subject,
                "api_key": raw_key,
                "reviewer_token": raw_token,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"seeded case {case_id} for tenant {tenant_id} -> {fixture_path}")


async def cleanup(fixture_path: Path) -> None:
    if not fixture_path.exists():
        return
    tenant_id = json.loads(fixture_path.read_text(encoding="utf-8"))["tenant_id"]
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            for table in (
                "audit_log",
                "review_decisions",
                "webhook_deliveries",
                "sanctions_hits",
                "cases",
                "reviewers",
                "api_keys",
                "risk_policies",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
    finally:
        await conn.close()
    fixture_path.unlink()
    print(f"removed tenant {tenant_id}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--cleanup"]:
        asyncio.run(cleanup(Path(args[1])))
    elif len(args) == 1:
        asyncio.run(seed(Path(args[0])))
    else:
        raise SystemExit(__doc__)
