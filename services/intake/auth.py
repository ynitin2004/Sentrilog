import hashlib
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

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
