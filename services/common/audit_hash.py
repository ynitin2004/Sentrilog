"""The single canonical row_hash formula for audit_log's tamper-evidence chain.

Both services/pipeline/audit.py (Temporal activities) and services/intake/audit.py (case
creation, review decisions) append to the *same* per-tenant chain from two separate processes
with two separate DB pools. If each computed row_hash with its own copy of this formula, the
two copies drifting apart by even one byte would silently fork the chain the first time both
services wrote to the same tenant back-to-back -- a bug that would only surface when someone
tried to verify the chain, long after the drift happened. Sharing one function makes that class
of bug impossible rather than something to keep in sync by hand.
"""

import hashlib
import json
from datetime import datetime

# ASCII record separator, not a printable delimiter like "|" -- a printable delimiter can appear
# inside a field (an actor string, a payload's JSON), which would let two different sets of
# field values hash identically by shifting a delimiter across a field boundary. \x1e can't
# appear in any of these fields (JSON strings escape control characters), so it can't collide.
_FIELD_SEPARATOR = "\x1e"


def canonical_payload_json(payload: dict[str, object]) -> str:
    """Deterministic JSON serialization -- sort_keys so the same payload dict always produces
    the same bytes to hash, regardless of the insertion order used to build it in Python."""
    return json.dumps(payload, sort_keys=True, default=str)


def compute_row_hash(
    *,
    tenant_id: str,
    case_id: str,
    event_type: str,
    actor: str,
    model_version: str | None,
    input_hash: str | None,
    payload_json: str,
    created_at: datetime,
    prev_row_hash: str | None,
) -> str:
    """row_hash(N) = sha256 over row N's own fields + row_hash(N-1). Changing anything about a
    historical row -- its payload, its actor, even its timestamp -- changes that row's own hash,
    which no longer matches what the *next* row recorded as prev_row_hash, breaking the chain
    from that point forward. That's the whole tamper-evidence property: it doesn't prevent
    someone with DB access from editing a row, it guarantees the edit is detectable.
    """
    canonical = _FIELD_SEPARATOR.join(
        [
            tenant_id,
            case_id,
            event_type,
            actor,
            model_version or "",
            input_hash or "",
            payload_json,
            created_at.isoformat(),
            prev_row_hash or "",
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
