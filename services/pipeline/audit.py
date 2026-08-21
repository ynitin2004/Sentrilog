"""Writes to audit_log -- the append-only, hash-chained tamper-evidence trail every Temporal
activity now records entry/exit rows to (see PLAN.md Phase 11). The table, its RLS policy, and
its INSERT-only grant (SELECT + INSERT, no UPDATE/DELETE for sentrilog_app) were already
scaffolded in Phase 1/2; this module is what actually writes to it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from services.common.audit_hash import canonical_payload_json, compute_row_hash

from . import db


async def record(
    tenant_id: str,
    case_id: str,
    event_type: str,
    *,
    actor: str,
    model_version: str | None = None,
    input_hash: str | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    """Appends one row to tenant_id's chain. Safe to call concurrently for the same tenant from
    multiple activities/processes -- see the advisory-lock comment below for why that isn't
    automatic."""
    payload_json = canonical_payload_json(payload or {})
    created_at = datetime.now(UTC)

    async with db.tenant_connection(tenant_id) as conn:
        # Computing row_hash requires reading the current last row, then inserting the next one
        # -- a plain SELECT-then-INSERT is a race: two concurrent activities for the same tenant
        # (different cases can process concurrently) could both read the same "last" row and
        # each compute a row_hash chained to it, forking the chain instead of extending it.
        # pg_advisory_xact_lock serializes writers on the same tenant_id (different tenants
        # don't block each other) and is held only for this transaction's lifetime, released
        # automatically when tenant_connection()'s `async with` commits.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('audit_log:' || $1))", tenant_id)

        prev_row = await conn.fetchrow(
            "SELECT row_hash FROM audit_log WHERE tenant_id = $1 ORDER BY id DESC LIMIT 1",
            tenant_id,
        )
        prev_row_hash = prev_row["row_hash"] if prev_row is not None else None

        row_hash = compute_row_hash(
            tenant_id=tenant_id,
            case_id=case_id,
            event_type=event_type,
            actor=actor,
            model_version=model_version,
            input_hash=input_hash,
            payload_json=payload_json,
            created_at=created_at,
            prev_row_hash=prev_row_hash,
        )

        await conn.execute(
            "INSERT INTO audit_log (tenant_id, case_id, event_type, actor, model_version, "
            "input_hash, payload, prev_row_hash, row_hash, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            tenant_id,
            case_id,
            event_type,
            actor,
            model_version,
            input_hash,
            payload_json,
            prev_row_hash,
            row_hash,
            created_at,
        )


@dataclass
class AuditExit:
    """Yielded by audited() -- set .payload/.model_version/.input_hash before the block ends and
    they're used for the '.completed' row; an exception raised inside the block produces a
    '.failed' row instead, with the exception message as its payload. input_hash lives here
    (not as an audited() constructor argument) because it usually isn't known until partway
    through the activity's own work -- e.g. it's a hash of bytes the activity itself fetches."""

    payload: dict[str, object] = field(default_factory=dict)
    model_version: str | None = None
    input_hash: str | None = None


@asynccontextmanager
async def audited(
    tenant_id: str,
    case_id: str,
    step: str,
    *,
    actor: str = "system:pipeline-worker",
    input_hash: str | None = None,
) -> AsyncIterator[AuditExit]:
    """Wraps one activity's real work with a `{step}.started` row before it and a
    `{step}.completed` or `{step}.failed` row after -- the entry/exit pairing Phase 11 asks for.
    A `.started` row with no matching `.completed`/`.failed` row is itself a real operational
    signal: the worker crashed mid-activity, exactly the failure mode Phase 12's "worker died
    mid-case" runbook needs to be able to detect.
    """
    await record(tenant_id, case_id, f"{step}.started", actor=actor, input_hash=input_hash)
    exit_info = AuditExit()
    try:
        yield exit_info
    except Exception as exc:
        await record(
            tenant_id,
            case_id,
            f"{step}.failed",
            actor=actor,
            payload={"error": str(exc)[:500]},
        )
        raise
    else:
        await record(
            tenant_id,
            case_id,
            f"{step}.completed",
            actor=actor,
            model_version=exit_info.model_version,
            input_hash=exit_info.input_hash,
            payload=exit_info.payload,
        )
