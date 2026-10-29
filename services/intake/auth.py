import hashlib
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from . import db


def hash_api_key(raw_key: str) -> str:
    # API keys are high-entropy random tokens, not user-chosen passwords -- a fast hash
    # (SHA-256) is the right tool here, unlike bcrypt/argon2 which defend against low-entropy
    # human passwords by being deliberately slow. Brute-forcing a 256-bit random token isn't
    # feasible regardless of hash speed.
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    api_key_id: str


@dataclass(frozen=True)
class ReviewerAuthContext:
    tenant_id: str
    reviewer_id: str
    role: str


async def require_api_key(authorization: str | None = Header(default=None)) -> AuthContext:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header; expected 'Bearer <api_key>'",
        )
    raw_key = authorization.removeprefix("Bearer ").strip()
    if not raw_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Empty API key")

    row = await db.resolve_api_key(hash_api_key(raw_key))
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key revoked")

    return AuthContext(tenant_id=str(row["tenant_id"]), api_key_id=str(row["api_key_id"]))


async def require_reviewer(
    authorization: str | None = Header(default=None),
) -> ReviewerAuthContext:
    # Same hash-and-resolve pattern as require_api_key -- reviewer tokens are high-entropy
    # random tokens too (see scripts/seed_dev_reviewer.py), not human-chosen passwords.
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header; expected 'Bearer <reviewer_token>'",
        )
    raw_token = authorization.removeprefix("Bearer ").strip()
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Empty reviewer token")

    row = await db.resolve_reviewer_token(hash_api_key(raw_token))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid reviewer token"
        )
    if row["revoked_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Reviewer token revoked"
        )

    return ReviewerAuthContext(
        tenant_id=str(row["tenant_id"]), reviewer_id=str(row["reviewer_id"]), role=row["role"]
    )


async def require_any_tenant(authorization: str | None = Header(default=None)) -> str:
    """Resolves either an API key or a reviewer token to its tenant_id.

    The event stream is one endpoint serving both the reviewer queue and the admin console, so
    it accepts whichever credential the caller has rather than forcing two separate routes.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header; expected 'Bearer <token>'",
        )
    raw_token = authorization.removeprefix("Bearer ").strip()
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Empty token")

    token_hash = hash_api_key(raw_token)

    reviewer_row = await db.resolve_reviewer_token(token_hash)
    if reviewer_row is not None and reviewer_row["revoked_at"] is None:
        return str(reviewer_row["tenant_id"])

    api_key_row = await db.resolve_api_key(token_hash)
    if api_key_row is not None and api_key_row["revoked_at"] is None:
        return str(api_key_row["tenant_id"])

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked token")


# Roles that may actually change a case's outcome. 'auditor' is deliberately excluded: an
# auditor's job in a KYC/AML system is to inspect the trail, not to approve or reject the cases
# in it, and before Phase 12 the role column was resolved and carried on every request but never
# checked -- so an auditor token could claim and decide exactly like a reviewer's.
_DECISION_MAKER_ROLES = frozenset({"reviewer", "admin"})


async def require_decision_maker(
    reviewer: ReviewerAuthContext = Depends(require_reviewer),
) -> ReviewerAuthContext:
    """403, not 404, unlike the cross-tenant checks elsewhere in this service.

    404-not-403 is right for cross-tenant access because the *existence* of another tenant's
    case is itself privileged information. Here the caller is inside their own tenant and can
    already see the case in their own queue, so there's nothing to conceal -- hiding it as a 404
    would just make a legitimate reviewer's permission problem undebuggable.
    """
    if reviewer.role not in _DECISION_MAKER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{reviewer.role}' may not claim or decide cases",
        )
    return reviewer
