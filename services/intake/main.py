from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from temporalio.service import RPCError, RPCStatusCode

from services.pipeline.workflows.contracts import (
    SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
    ReviewDecisionSignal,
)

from . import db
from .auth import AuthContext, ReviewerAuthContext, require_api_key, require_reviewer
from .config import settings
from .ratelimit import InMemoryRateLimiter
from .scanning import validate_upload_request
from .schemas import (
    CaseCreateRequest,
    CaseCreateResponse,
    CaseResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewQueueCase,
    UploadTarget,
)
from .storage import presigned_put_url
from .temporal import get_temporal_client, start_kyc_case_workflow, workflow_id_for_case

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

# The reviewer UI (webui/reviewer.html) is a standalone static file opened directly in a
# browser, not served by this app -- it sends Origin: null / a file:// origin. Auth here is a
# Bearer token header, not cookies, so a wildcard origin doesn't expose credentialed requests
# the way allow_origins=["*"] with allow_credentials=True would.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_rate_limit(auth: AuthContext) -> None:
    _check_rate_limit_key(auth.api_key_id)


def _check_rate_limit_key(key: str) -> None:
    allowed, retry_after = _rate_limiter.check(key)
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

        plan_tier = await conn.fetchval(
            "SELECT plan_tier FROM tenants WHERE id = $1", auth.tenant_id
        )

    # Outside the DB transaction: starting a workflow is an external side effect, not
    # transactional with Postgres. Known gap, not silent: if this fails after the case row
    # already committed, the case is left in 'pending' with no workflow driving it forward --
    # a transactional-outbox pattern would close this properly; not built here (see PLAN.md
    # Phase 5 changelog). start_kyc_case_workflow is itself idempotent on case_id, so replays
    # (including this same request retried by the client) don't double-start anything.
    await start_kyc_case_workflow(tenant_id=auth.tenant_id, case_id=case_id, plan_tier=plan_tier)

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


def _parse_case_id_or_404(case_id: str) -> None:
    try:
        UUID(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found") from exc


@app.get("/review/cases", response_model=list[ReviewQueueCase])
async def list_review_queue(
    reviewer: ReviewerAuthContext = Depends(require_reviewer),
) -> list[ReviewQueueCase]:
    """The review queue *is* `cases WHERE status = 'needs_review'` (see PLAN.md) -- there's no
    separate queue table to keep in sync."""
    _check_rate_limit_key(reviewer.reviewer_id)

    async with db.tenant_connection(reviewer.tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, subject_name, subject_dob, risk_score, created_at, "
            "claimed_by_reviewer_id, claimed_at FROM cases "
            "WHERE status = 'needs_review' ORDER BY created_at ASC"
        )

    return [
        ReviewQueueCase(
            case_id=str(row["id"]),
            subject_name=row["subject_name"],
            subject_dob=row["subject_dob"],
            risk_score=float(row["risk_score"]) if row["risk_score"] is not None else None,
            created_at=row["created_at"],
            claimed_by_reviewer_id=(
                str(row["claimed_by_reviewer_id"])
                if row["claimed_by_reviewer_id"] is not None
                else None
            ),
            claimed_at=row["claimed_at"],
        )
        for row in rows
    ]


@app.post("/review/cases/{case_id}/claim", response_model=ReviewQueueCase)
async def claim_review_case(
    case_id: str, reviewer: ReviewerAuthContext = Depends(require_reviewer)
) -> ReviewQueueCase:
    """Advisory only (see PLAN.md): claiming is a UI signal, not a lock -- the decision
    endpoint below doesn't require a prior claim, so a stale/abandoned claim can never block a
    case from being decided. Re-claiming (by the same or a different reviewer) simply
    overwrites the previous claim."""
    _check_rate_limit_key(reviewer.reviewer_id)
    _parse_case_id_or_404(case_id)

    async with db.tenant_connection(reviewer.tenant_id) as conn:
        row = await conn.fetchrow(
            "UPDATE cases SET claimed_by_reviewer_id = $1, claimed_at = now() "
            "WHERE id = $2 AND status = 'needs_review' "
            "RETURNING id, subject_name, subject_dob, risk_score, created_at, "
            "claimed_by_reviewer_id, claimed_at",
            reviewer.reviewer_id,
            case_id,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found or not awaiting review"
        )

    return ReviewQueueCase(
        case_id=str(row["id"]),
        subject_name=row["subject_name"],
        subject_dob=row["subject_dob"],
        risk_score=float(row["risk_score"]) if row["risk_score"] is not None else None,
        created_at=row["created_at"],
        claimed_by_reviewer_id=str(row["claimed_by_reviewer_id"]),
        claimed_at=row["claimed_at"],
    )


@app.post("/review/cases/{case_id}/decision", response_model=ReviewDecisionResponse)
async def submit_review_decision(
    case_id: str,
    payload: ReviewDecisionRequest,
    reviewer: ReviewerAuthContext = Depends(require_reviewer),
) -> ReviewDecisionResponse:
    _check_rate_limit_key(reviewer.reviewer_id)
    _parse_case_id_or_404(case_id)

    async with db.tenant_connection(reviewer.tenant_id) as conn:
        case_status = await conn.fetchval("SELECT status FROM cases WHERE id = $1", case_id)
        if case_status is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
        if case_status != "needs_review":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Case is not awaiting review (status: {case_status})",
            )

        decided_at = await conn.fetchval(
            "INSERT INTO review_decisions (tenant_id, case_id, reviewer_id, decision, "
            "justification) VALUES ($1, $2, $3, $4, $5) RETURNING decided_at",
            reviewer.tenant_id,
            case_id,
            reviewer.reviewer_id,
            payload.decision,
            payload.justification,
        )

    # Signaling the workflow is an external side effect outside the DB transaction above, same
    # known-gap tradeoff create_case makes for start_kyc_case_workflow (see PLAN.md Phase 5
    # changelog): if this fails after the decision row already committed, the case is left
    # needs_review with a recorded decision but no workflow progress. A transactional outbox
    # would close this properly; not built here.
    client = await get_temporal_client()
    try:
        handle = client.get_workflow_handle(workflow_id_for_case(case_id))
        await handle.signal(
            SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
            ReviewDecisionSignal(
                reviewer_id=reviewer.reviewer_id,
                decision=payload.decision,
                justification=payload.justification,
            ),
        )
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Case's workflow is no longer running; decision recorded but not applied",
            ) from exc
        raise

    return ReviewDecisionResponse(
        case_id=case_id,
        decision=payload.decision,
        reviewer_id=reviewer.reviewer_id,
        decided_at=decided_at,
    )
