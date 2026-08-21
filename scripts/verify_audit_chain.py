"""Walks audit_log's hash chain and reports any break -- the tamper-evidence check Phase 11's
exit criteria calls for: manually editing a historical row must make this fail.

Verification is scoped per tenant (each tenant's rows form their own independent chain -- see
services/pipeline/audit.py's record() for why), not one chain across the whole table.

Usage: uv run python scripts/verify_audit_chain.py [tenant-id]
Exits 0 and prints "OK" if every tenant's chain (or just the given one) is intact; exits 1 and
prints every break found otherwise. With no tenant-id, checks every tenant that has any
audit_log rows.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from services.common.audit_hash import compute_row_hash  # noqa: E402

# Deliberately the superuser/table-owner role, not sentrilog_app's DSN -- RLS is a no-op for
# this role (002_multi_tenancy.sql), which is exactly what a cross-tenant verification tool
# needs: sentrilog_app's own RLS policy scopes every query to whatever app.tenant_id the *caller*
# has set, so a plain connection as that role querying audit_log with no tenant context set
# would see zero rows for every tenant, not "all of them." An auditor legitimately needs the
# access a normal request never should.
_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"


def verify_chain(rows: list[asyncpg.Record]) -> list[str]:
    """rows must already be ordered by id ascending (a single tenant's rows, oldest first).
    Returns a list of human-readable violation descriptions; empty means the chain is intact.
    """
    violations = []
    expected_prev_hash: str | None = None

    for row in rows:
        if row["prev_row_hash"] != expected_prev_hash:
            violations.append(
                f"audit_log.id={row['id']}: prev_row_hash does not match the preceding row's "
                f"row_hash (expected {expected_prev_hash!r}, found {row['prev_row_hash']!r}) "
                "-- a row may have been inserted, deleted, or reordered"
            )

        recomputed = compute_row_hash(
            tenant_id=str(row["tenant_id"]),
            case_id=str(row["case_id"]),
            event_type=row["event_type"],
            actor=row["actor"],
            model_version=row["model_version"],
            input_hash=row["input_hash"],
            payload_json=row["payload"],
            created_at=row["created_at"],
            prev_row_hash=row["prev_row_hash"],
        )
        if recomputed != row["row_hash"]:
            violations.append(
                f"audit_log.id={row['id']}: stored row_hash does not match a hash recomputed "
                "from this row's own fields -- this row's payload, actor, model_version, "
                "input_hash, created_at, or prev_row_hash was altered after it was written"
            )

        expected_prev_hash = row["row_hash"]

    return violations


async def _fetch_tenant_ids(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch("SELECT DISTINCT tenant_id FROM audit_log")
    return [str(r["tenant_id"]) for r in rows]


async def _fetch_chain(conn: asyncpg.Connection, tenant_id: str) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await conn.fetch(
        "SELECT id, tenant_id, case_id, event_type, actor, model_version, input_hash, "
        "payload, prev_row_hash, row_hash, created_at FROM audit_log "
        "WHERE tenant_id = $1 ORDER BY id ASC",
        tenant_id,
    )
    return rows


async def main() -> int:
    tenant_arg = sys.argv[1] if len(sys.argv) > 1 else None

    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        tenant_ids = [tenant_arg] if tenant_arg else await _fetch_tenant_ids(conn)
        if not tenant_ids:
            print("No audit_log rows found for any tenant.")
            return 0

        all_violations: list[str] = []
        for tenant_id in tenant_ids:
            rows = await _fetch_chain(conn, tenant_id)
            violations = verify_chain(rows)
            if violations:
                all_violations.append(f"tenant {tenant_id}: {len(rows)} row(s), chain BROKEN")
                all_violations.extend(f"  {v}" for v in violations)
            else:
                print(f"tenant {tenant_id}: {len(rows)} row(s), chain OK")
    finally:
        await conn.close()

    if all_violations:
        print("\n".join(all_violations))
        return 1

    print("OK: all audit chains intact")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
