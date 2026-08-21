import asyncio
import json
import secrets
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from temporalio.service import RPCError, RPCStatusCode

from services.pipeline.workflows.contracts import (
    SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
    ReviewDecisionSignal,
)

from . import audit, db
from .auth import (
    AuthContext,
    ReviewerAuthContext,
    hash_api_key,
    require_any_tenant,
    require_api_key,
    require_reviewer,
)
from .config import settings
from .ratelimit import InMemoryRateLimiter
from .scanning import validate_upload_request
from .schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    CaseCreateRequest,
    CaseCreateResponse,
    CaseResponse,
    DashboardActivityItem,
    DashboardCaseVolumePoint,
    DashboardSummaryResponse,
    ExtractionDetail,
    FaceMatchDetail,
    ReviewCaseDetailResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewerCreateRequest,
    ReviewerCreateResponse,
    ReviewerResponse,
    ReviewQueueCase,
    SanctionsHitDetail,
    UploadTarget,
    WebhookCreateRequest,
    WebhookCreateResponse,
    WebhookDeliveryResponse,
    WebhookResponse,
)
from .storage import presigned_get_url, presigned_put_url
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

    if not is_replay:
        # Not written for a replay (an idempotency-key retry of an already-created case) -- the
        # case wasn't actually created again, so an audit row saying it was would misrepresent
        # the trail rather than complete it.
        await audit.record(
            auth.tenant_id,
            case_id,
            "case_created",
            actor=f"api-key:{auth.api_key_id}",
            payload={"subject_name": payload.subject_name, "plan_tier": plan_tier},
        )

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
            "SELECT id, status, subject_name, subject_dob, decision, created_at, risk_score "
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
        risk_score=float(row["risk_score"]) if row["risk_score"] is not None else None,
    )


_CASE_STATUSES = ("pending", "processing", "needs_review", "approved", "rejected")


@app.get("/cases", response_model=list[CaseResponse])
async def list_cases(
    auth: AuthContext = Depends(require_api_key),
    status_filter: (
        Literal["pending", "processing", "needs_review", "approved", "rejected"] | None
    ) = Query(default=None, alias="status"),
) -> list[CaseResponse]:
    """Tenant-wide, distinct from /review/cases (the needs_review-only queue) -- the admin
    console's case list, not the reviewer's."""
    _check_rate_limit(auth)

    async with db.tenant_connection(auth.tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, status, subject_name, subject_dob, decision, created_at, risk_score "
            "FROM cases WHERE ($1::text IS NULL OR status = $1) "
            "ORDER BY created_at DESC LIMIT 200",
            status_filter,
        )

    return [
        CaseResponse(
            case_id=str(row["id"]),
            status=row["status"],
            subject_name=row["subject_name"],
            subject_dob=row["subject_dob"],
            decision=row["decision"],
            created_at=row["created_at"],
            risk_score=float(row["risk_score"]) if row["risk_score"] is not None else None,
        )
        for row in rows
    ]


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


@app.get("/review/cases/{case_id}", response_model=ReviewCaseDetailResponse)
async def get_review_case_detail(
    case_id: str, reviewer: ReviewerAuthContext = Depends(require_reviewer)
) -> ReviewCaseDetailResponse:
    """Everything a reviewer needs to actually make a decision -- the queue list endpoint above
    deliberately only has enough to render a row; this one composes the case with its
    extraction, face match, and sanctions hits, which the admin-facing GET /cases doesn't need
    and doesn't return."""
    _check_rate_limit_key(reviewer.reviewer_id)
    _parse_case_id_or_404(case_id)

    async with db.tenant_connection(reviewer.tenant_id) as conn:
        case_row = await conn.fetchrow(
            "SELECT id, status, subject_name, subject_dob, decision, created_at, risk_score "
            "FROM cases WHERE id = $1",
            case_id,
        )
        if case_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

        document_rows = await conn.fetch(
            "SELECT doc_type, s3_key FROM documents WHERE case_id = $1", case_id
        )
        extraction_row = await conn.fetchrow(
            "SELECT document_id, model_version, raw_json, confidence, valid "
            "FROM extractions WHERE case_id = $1 ORDER BY id DESC LIMIT 1",
            case_id,
        )
        face_match_row = await conn.fetchrow(
            "SELECT similarity_score FROM face_matches WHERE case_id = $1 "
            "ORDER BY id DESC LIMIT 1",
            case_id,
        )
        sanctions_rows = await conn.fetch(
            "SELECT list_source, matched_name, match_score, method "
            "FROM sanctions_hits WHERE case_id = $1",
            case_id,
        )

    s3_key_by_type = {row["doc_type"]: row["s3_key"] for row in document_rows}

    extraction: ExtractionDetail | None = None
    if extraction_row is not None:
        document = json.loads(extraction_row["raw_json"]) if extraction_row["raw_json"] else {}
        extraction = ExtractionDetail(
            document_id=str(extraction_row["document_id"]),
            method="mrz" if extraction_row["model_version"] == "mrz-icao9303" else "vlm",
            confidence=extraction_row["confidence"],
            valid=extraction_row["valid"],
            full_name=document.get("full_name"),
            date_of_birth=document.get("date_of_birth"),
            document_number=document.get("document_number"),
            nationality=document.get("nationality"),
            expiry_date=document.get("expiry_date"),
        )

    face_match: FaceMatchDetail | None = None
    if face_match_row is not None:
        score = face_match_row["similarity_score"]
        face_match = FaceMatchDetail(
            similarity_score=float(score) if score is not None else None,
            reason=None if score is not None else "No face detected in one or both images",
        )

    return ReviewCaseDetailResponse(
        case_id=str(case_row["id"]),
        status=case_row["status"],
        subject_name=case_row["subject_name"],
        subject_dob=case_row["subject_dob"],
        decision=case_row["decision"],
        created_at=case_row["created_at"],
        risk_score=(float(case_row["risk_score"]) if case_row["risk_score"] is not None else None),
        id_document_url=(
            presigned_get_url(s3_key_by_type["id_document"])
            if "id_document" in s3_key_by_type
            else None
        ),
        selfie_url=(
            presigned_get_url(s3_key_by_type["selfie"]) if "selfie" in s3_key_by_type else None
        ),
        extraction=extraction,
        face_match=face_match,
        sanctions_hits=[
            SanctionsHitDetail(
                list_source=row["list_source"],
                matched_name=row["matched_name"],
                match_score=row["match_score"],
                method=row["method"],
            )
            for row in sanctions_rows
        ],
    )


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
        if row is not None:
            await conn.execute(
                "SELECT pg_notify('case_events', $1)",
                json.dumps(
                    {
                        "tenant_id": reviewer.tenant_id,
                        "case_id": case_id,
                        "status": "needs_review",
                        "decision": None,
                        "claimed_by_reviewer_id": reviewer.reviewer_id,
                    }
                ),
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

    # Recorded as soon as the decision itself commits, regardless of whether signaling the
    # workflow below succeeds -- the decision was genuinely made at this point even in the known
    # "workflow no longer running" case a few lines down, and the audit trail should say so.
    await audit.record(
        reviewer.tenant_id,
        case_id,
        "review_decision_recorded",
        actor=f"reviewer:{reviewer.reviewer_id}",
        payload={"decision": payload.decision, "justification": payload.justification},
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


def _parse_uuid_or_404(value: str, resource: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource} not found"
        ) from exc


# --- API keys -----------------------------------------------------------------------------
# No fine-grained admin-vs-operational key tier: any valid API key for a tenant can manage
# that tenant's own keys/webhooks/reviewers (see PLAN.md Phase 9's design decisions) -- the
# first key per tenant is still bootstrapped via scripts/seed_dev_tenant.py; these endpoints
# are how every key after that gets created.


@app.post("/api-keys", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreateRequest, auth: AuthContext = Depends(require_api_key)
) -> ApiKeyCreateResponse:
    _check_rate_limit(auth)
    raw_key = secrets.token_urlsafe(32)

    async with db.tenant_connection(auth.tenant_id) as conn:
        row = await conn.fetchrow(
            "INSERT INTO api_keys (tenant_id, key_hash, name) VALUES ($1, $2, $3) "
            "RETURNING id, name, created_at, revoked_at",
            auth.tenant_id,
            hash_api_key(raw_key),
            payload.name,
        )

    assert row is not None
    return ApiKeyCreateResponse(
        id=str(row["id"]),
        name=row["name"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        raw_key=raw_key,
    )


@app.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(auth: AuthContext = Depends(require_api_key)) -> list[ApiKeyResponse]:
    _check_rate_limit(auth)

    async with db.tenant_connection(auth.tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, name, created_at, revoked_at FROM api_keys ORDER BY created_at DESC"
        )

    return [
        ApiKeyResponse(
            id=str(row["id"]),
            name=row["name"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
        )
        for row in rows
    ]


@app.post("/api-keys/{key_id}/revoke", response_model=ApiKeyResponse)
async def revoke_api_key(
    key_id: str, auth: AuthContext = Depends(require_api_key)
) -> ApiKeyResponse:
    _check_rate_limit(auth)
    _parse_uuid_or_404(key_id, "API key")

    # COALESCE, not a plain SET: re-revoking an already-revoked key is a no-op success (idempotent),
    # not a 404 -- only a genuinely nonexistent or cross-tenant key_id is a 404. Revoking your own
    # currently-in-use key is allowed and not specially protected against -- an accepted footgun,
    # documented rather than papered over with unrequested last-key protection (see PLAN.md).
    async with db.tenant_connection(auth.tenant_id) as conn:
        row = await conn.fetchrow(
            "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, now()) WHERE id = $1 "
            "RETURNING id, name, created_at, revoked_at",
            key_id,
        )

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    return ApiKeyResponse(
        id=str(row["id"]),
        name=row["name"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


# --- Reviewers ------------------------------------------------------------------------------


@app.post("/reviewers", response_model=ReviewerCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_reviewer(
    payload: ReviewerCreateRequest, auth: AuthContext = Depends(require_api_key)
) -> ReviewerCreateResponse:
    _check_rate_limit(auth)
    raw_token = secrets.token_urlsafe(32)

    try:
        async with db.tenant_connection(auth.tenant_id) as conn:
            row = await conn.fetchrow(
                "INSERT INTO reviewers (tenant_id, email, role, token_hash) "
                "VALUES ($1, $2, $3, $4) RETURNING id, email, role, created_at, revoked_at",
                auth.tenant_id,
                payload.email,
                payload.role,
                hash_api_key(raw_token),
            )
    except asyncpg.UniqueViolationError as exc:
        # reviewers has UNIQUE (tenant_id, email) -- a second reviewer with the same email for
        # this tenant is a real, expected conflict, not a 500.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A reviewer with this email already exists",
        ) from exc

    assert row is not None
    return ReviewerCreateResponse(
        id=str(row["id"]),
        email=row["email"],
        role=row["role"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        raw_token=raw_token,
    )


@app.get("/reviewers", response_model=list[ReviewerResponse])
async def list_reviewers(auth: AuthContext = Depends(require_api_key)) -> list[ReviewerResponse]:
    _check_rate_limit(auth)

    async with db.tenant_connection(auth.tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, email, role, created_at, revoked_at FROM reviewers "
            "ORDER BY created_at DESC"
        )

    return [
        ReviewerResponse(
            id=str(row["id"]),
            email=row["email"],
            role=row["role"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
        )
        for row in rows
    ]


@app.post("/reviewers/{reviewer_id}/revoke", response_model=ReviewerResponse)
async def revoke_reviewer(
    reviewer_id: str, auth: AuthContext = Depends(require_api_key)
) -> ReviewerResponse:
    _check_rate_limit(auth)
    _parse_uuid_or_404(reviewer_id, "Reviewer")

    async with db.tenant_connection(auth.tenant_id) as conn:
        row = await conn.fetchrow(
            "UPDATE reviewers SET revoked_at = COALESCE(revoked_at, now()) WHERE id = $1 "
            "RETURNING id, email, role, created_at, revoked_at",
            reviewer_id,
        )

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reviewer not found")

    return ReviewerResponse(
        id=str(row["id"]),
        email=row["email"],
        role=row["role"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


# --- Webhooks -------------------------------------------------------------------------------


@app.post("/webhooks", response_model=WebhookCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreateRequest, auth: AuthContext = Depends(require_api_key)
) -> WebhookCreateResponse:
    _check_rate_limit(auth)
    secret = secrets.token_urlsafe(32)

    async with db.tenant_connection(auth.tenant_id) as conn:
        row = await conn.fetchrow(
            "INSERT INTO webhooks (tenant_id, url, secret) VALUES ($1, $2, $3) "
            "RETURNING id, url, created_at, disabled_at",
            auth.tenant_id,
            payload.url,
            secret,
        )

    assert row is not None
    return WebhookCreateResponse(
        id=str(row["id"]),
        url=row["url"],
        created_at=row["created_at"],
        disabled_at=row["disabled_at"],
        secret=secret,
    )


@app.get("/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(auth: AuthContext = Depends(require_api_key)) -> list[WebhookResponse]:
    _check_rate_limit(auth)

    async with db.tenant_connection(auth.tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, url, created_at, disabled_at FROM webhooks ORDER BY created_at DESC"
        )

    return [
        WebhookResponse(
            id=str(row["id"]),
            url=row["url"],
            created_at=row["created_at"],
            disabled_at=row["disabled_at"],
        )
        for row in rows
    ]


@app.post("/webhooks/{webhook_id}/disable", response_model=WebhookResponse)
async def disable_webhook(
    webhook_id: str, auth: AuthContext = Depends(require_api_key)
) -> WebhookResponse:
    _check_rate_limit(auth)
    _parse_uuid_or_404(webhook_id, "Webhook")

    async with db.tenant_connection(auth.tenant_id) as conn:
        row = await conn.fetchrow(
            "UPDATE webhooks SET disabled_at = COALESCE(disabled_at, now()) WHERE id = $1 "
            "RETURNING id, url, created_at, disabled_at",
            webhook_id,
        )

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    return WebhookResponse(
        id=str(row["id"]),
        url=row["url"],
        created_at=row["created_at"],
        disabled_at=row["disabled_at"],
    )


@app.get("/webhooks/{webhook_id}/deliveries", response_model=list[WebhookDeliveryResponse])
async def list_webhook_deliveries(
    webhook_id: str, auth: AuthContext = Depends(require_api_key)
) -> list[WebhookDeliveryResponse]:
    _check_rate_limit(auth)
    _parse_uuid_or_404(webhook_id, "Webhook")

    async with db.tenant_connection(auth.tenant_id) as conn:
        webhook_exists = await conn.fetchval("SELECT 1 FROM webhooks WHERE id = $1", webhook_id)
        if webhook_exists is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

        rows = await conn.fetch(
            "SELECT id, webhook_id, case_id, event_type, status, attempt_count, "
            "last_attempted_at FROM webhook_deliveries WHERE webhook_id = $1 "
            "ORDER BY last_attempted_at DESC NULLS LAST",
            webhook_id,
        )

    return [
        WebhookDeliveryResponse(
            id=str(row["id"]),
            webhook_id=str(row["webhook_id"]),
            case_id=str(row["case_id"]),
            event_type=row["event_type"],
            status=row["status"],
            attempt_count=row["attempt_count"],
            last_attempted_at=row["last_attempted_at"],
        )
        for row in rows
    ]


# --- Dashboard --------------------------------------------------------------------------


@app.get("/dashboard/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    auth: AuthContext = Depends(require_api_key),
) -> DashboardSummaryResponse:
    _check_rate_limit(auth)

    async with db.tenant_connection(auth.tenant_id) as conn:
        status_rows = await conn.fetch(
            "SELECT status, count(*) AS count FROM cases GROUP BY status"
        )
        volume_rows = await conn.fetch(
            "SELECT date_trunc('week', created_at)::date AS week, count(*) AS count "
            "FROM cases WHERE created_at > now() - interval '30 days' "
            "GROUP BY 1 ORDER BY 1"
        )
        # Two real activity sources (case creation, review decisions -- the latter joined to
        # reviewers for a human-readable "approved by x@y.com" rather than a bare reviewer_id),
        # merged and capped at 10 -- not a generic activity-log table, which would be
        # over-engineering ahead of an actual need for this console's first dashboard.
        activity_rows = await conn.fetch(
            "SELECT case_id, subject_name, event, at FROM ("
            "  SELECT c.id AS case_id, c.subject_name, 'case created' AS event, "
            "         c.created_at AS at "
            "  FROM cases c ORDER BY c.created_at DESC LIMIT 10"
            ") created "
            "UNION ALL "
            "SELECT case_id, subject_name, event, at FROM ("
            "  SELECT rd.case_id, c.subject_name, rd.decision || ' by ' || r.email AS event, "
            "         rd.decided_at AS at "
            "  FROM review_decisions rd "
            "  JOIN cases c ON c.id = rd.case_id "
            "  JOIN reviewers r ON r.id = rd.reviewer_id "
            "  ORDER BY rd.decided_at DESC LIMIT 10"
            ") decided "
            "ORDER BY at DESC LIMIT 10"
        )

    # Every known status always present (as 0), not just the ones with rows -- the frontend
    # dashboard reads specific keys directly (counts.needs_review, counts.approved, ...) and a
    # missing key would render as NaN, not 0.
    status_counts: dict[str, int] = dict.fromkeys(_CASE_STATUSES, 0)
    status_counts.update({row["status"]: row["count"] for row in status_rows})

    return DashboardSummaryResponse(
        status_counts=status_counts,
        cases_last_30_days=[
            DashboardCaseVolumePoint(date=row["week"], count=row["count"]) for row in volume_rows
        ],
        recent_activity=[
            DashboardActivityItem(
                case_id=str(row["case_id"]),
                subject_name=row["subject_name"],
                event=row["event"],
                at=row["at"],
            )
            for row in activity_rows
        ],
    )


# --- Real-time events (SSE) --------------------------------------------------------------


_SSE_KEEPALIVE_SECONDS = 15.0


async def _case_events_generator(request: Request, tenant_id: str) -> AsyncGenerator[str, None]:
    # A dedicated connection (not from the pool) held open for as long as the client stays
    # connected -- see db.raw_connection()'s docstring for why tenant_connection() can't do
    # this. Filtering by tenant happens here, in the callback, not in Postgres: pg_notify has
    # one shared channel across all tenants, and NOTIFY/LISTEN has no concept of row-level
    # security to filter it for us.
    queue: asyncio.Queue[str] = asyncio.Queue()

    def _on_notify(_conn: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        if event.get("tenant_id") == tenant_id:
            queue.put_nowait(payload)

    conn = await db.raw_connection()
    try:
        await conn.add_listener("case_events", _on_notify)
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_SECONDS)
            except TimeoutError:
                # A comment line (starts with ':') keeps intermediary proxies/load balancers
                # from treating a quiet-but-healthy connection as dead and closing it.
                yield ": keep-alive\n\n"
                continue
            yield f"event: case_status_changed\ndata: {payload}\n\n"
    finally:
        await conn.remove_listener("case_events", _on_notify)
        await conn.close()


@app.get("/events/stream")
async def stream_case_events(
    request: Request, tenant_id: str = Depends(require_any_tenant)
) -> StreamingResponse:
    return StreamingResponse(
        _case_events_generator(request, tenant_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disables response buffering on nginx-style reverse proxies, which would otherwise
            # hold the stream's bytes until a buffer filled instead of flushing them live.
            "X-Accel-Buffering": "no",
        },
    )
