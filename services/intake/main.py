from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, status

from . import db
from .auth import AuthContext, require_api_key
from .config import settings
from .ratelimit import InMemoryRateLimiter
from .scanning import validate_upload_request
from .schemas import CaseCreateRequest, CaseCreateResponse, CaseResponse, UploadTarget
from .storage import presigned_put_url

_rate_limiter = InMemoryRateLimiter(
    limit=settings.rate_limit_requests, window_seconds=settings.rate_limit_window_seconds
)

_DOC_TYPES = ("id_document", "selfie")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await db.init_pool()
    yield
    await db.close_pool()


app = FastAPI(title="Sentrilog Intake API", lifespan=lifespan)


def _check_rate_limit(auth: AuthContext) -> None:
    allowed, retry_after = _rate_limiter.check(auth.api_key_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


@app.post("/cases", response_model=CaseCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    payload: CaseCreateRequest,
    auth: AuthContext = Depends(require_api_key),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CaseCreateResponse:
    _check_rate_limit(auth)

    uploads = {"id_document": payload.id_document, "selfie": payload.selfie}
    for label, upload in uploads.items():
        error = validate_upload_request(
            upload.content_type, upload.size_bytes, settings.max_upload_bytes
        )
        if error is not None:
            raise HTTPException(status_code=422, detail=f"{label}: {error}")

    async with db.tenant_connection(auth.tenant_id) as conn:
        is_replay = False
        if idempotency_key:
            case_row = await conn.fetchrow(
                "INSERT INTO cases (tenant_id, subject_name, subject_dob, idempotency_key) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING "
                "RETURNING id, status",
                auth.tenant_id,
                payload.subject_name,
                payload.subject_dob,
                idempotency_key,
            )
            if case_row is None:
                is_replay = True
                case_row = await conn.fetchrow(
                    "SELECT id, status FROM cases WHERE tenant_id = $1 AND idempotency_key = $2",
                    auth.tenant_id,
                    idempotency_key,
                )
        else:
            case_row = await conn.fetchrow(
                "INSERT INTO cases (tenant_id, subject_name, subject_dob) "
                "VALUES ($1, $2, $3) RETURNING id, status",
                auth.tenant_id,
                payload.subject_name,
                payload.subject_dob,
            )

        assert case_row is not None
        case_id = str(case_row["id"])

        if is_replay:
            existing = await conn.fetch(
                "SELECT id, doc_type, s3_key FROM documents WHERE case_id = $1", case_id
            )
            doc_by_type = {d["doc_type"]: d for d in existing}
        else:
            doc_by_type = {}
            for doc_type in _DOC_TYPES:
                s3_key = f"{auth.tenant_id}/{case_id}/{doc_type}"
                doc_row = await conn.fetchrow(
                    "INSERT INTO documents (tenant_id, case_id, s3_key, doc_type) "
                    "VALUES ($1, $2, $3, $4) RETURNING id, doc_type, s3_key",
                    auth.tenant_id,
                    case_id,
                    s3_key,
                    doc_type,
                )
                assert doc_row is not None
                doc_by_type[doc_type] = doc_row

    targets: dict[str, UploadTarget] = {}
    for doc_type in _DOC_TYPES:
        doc = doc_by_type[doc_type]
        targets[doc_type] = UploadTarget(
            document_id=str(doc["id"]),
            s3_key=doc["s3_key"],
            upload_url=presigned_put_url(doc["s3_key"], uploads[doc_type].content_type),
        )

    return CaseCreateResponse(
        case_id=case_id,
        status=case_row["status"],
        id_document=targets["id_document"],
        selfie=targets["selfie"],
    )


@app.get("/cases/{case_id}", response_model=CaseResponse)
async def get_case(case_id: str, auth: AuthContext = Depends(require_api_key)) -> CaseResponse:
    _check_rate_limit(auth)
    try:
        UUID(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found") from exc

    async with db.tenant_connection(auth.tenant_id) as conn:
        # RLS scopes this to auth.tenant_id automatically -- a real case_id belonging to a
        # different tenant simply returns no row here, which becomes an honest 404 below
        # rather than leaking that the case exists at all.
        row = await conn.fetchrow(
            "SELECT id, status, subject_name, subject_dob, decision, created_at "
            "FROM cases WHERE id = $1",
            case_id,
        )

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    return CaseResponse(
        case_id=str(row["id"]),
        status=row["status"],
        subject_name=row["subject_name"],
        subject_dob=row["subject_dob"],
        decision=row["decision"],
        created_at=row["created_at"],
    )
