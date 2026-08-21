"""Intake API's side of audit_log writes -- case creation and review decisions, the two
human/API-triggered events that bookend a case's lifecycle. The Temporal-activity side (the
automated processing steps in between) lives in services/pipeline/audit.py; both append to the
same per-tenant hash chain via the same canonical formula in services/common/audit_hash.py, so a
case's full audit trail interleaves both services' rows in the order they actually happened.
"""

from datetime import UTC, datetime

from services.common.audit_hash import canonical_payload_json, compute_row_hash

from . import db


async def record(
    tenant_id: str,
    case_id: str,
    event_type: str,
    *,
    actor: str,
    payload: dict[str, object] | None = None,
) -> None:
    """See services/pipeline/audit.py's record() for the advisory-lock race it guards against --
    identical logic, duplicated here (not imported across the service boundary) the same way
    tenant_connection() is duplicated in each service's own db.py, matching this project's
    established pattern of self-contained, independently deployable services.
    """
    payload_json = canonical_payload_json(payload or {})
    created_at = datetime.now(UTC)

    async with db.tenant_connection(tenant_id) as conn:
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
            model_version=None,
            input_hash=None,
            payload_json=payload_json,
            created_at=created_at,
            prev_row_hash=prev_row_hash,
        )

        await conn.execute(
            "INSERT INTO audit_log (tenant_id, case_id, event_type, actor, model_version, "
            "input_hash, payload, prev_row_hash, row_hash, created_at) "
            "VALUES ($1, $2, $3, $4, NULL, NULL, $5, $6, $7, $8)",
            tenant_id,
            case_id,
            event_type,
            actor,
            payload_json,
            prev_row_hash,
            row_hash,
            created_at,
        )
