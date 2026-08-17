from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, field_validator

# Matches the cases.status / cases.decision CHECK constraints exactly (001_schema.sql) --
# declared as a real Literal, not `str`, so the generated OpenAPI schema (and therefore the
# frontend's generated TS types, via `npm run generate-types`) carries the narrow union instead
# of degrading to a bare string the frontend would need to re-cast at every call site.
CaseStatus = Literal["pending", "processing", "needs_review", "approved", "rejected"]
CaseDecision = Literal["approved", "rejected"]


class DocumentUpload(BaseModel):
    content_type: str
    size_bytes: int


class CaseCreateRequest(BaseModel):
    subject_name: str
    subject_dob: date | None = None
    id_document: DocumentUpload
    selfie: DocumentUpload


class UploadTarget(BaseModel):
    document_id: str
    s3_key: str
    upload_url: str


class CaseCreateResponse(BaseModel):
    case_id: str
    status: CaseStatus
    id_document: UploadTarget
    selfie: UploadTarget


class CaseResponse(BaseModel):
    case_id: str
    status: CaseStatus
    subject_name: str
    subject_dob: date | None
    decision: CaseDecision | None
    created_at: datetime
    risk_score: float | None


class ReviewQueueCase(BaseModel):
    case_id: str
    subject_name: str
    subject_dob: date | None
    risk_score: float | None
    created_at: datetime
    claimed_by_reviewer_id: str | None
    claimed_at: datetime | None


class ExtractionDetail(BaseModel):
    document_id: str
    method: Literal["mrz", "vlm"]
    confidence: float
    valid: bool
    full_name: str | None
    date_of_birth: date | None
    document_number: str | None
    nationality: str | None
    expiry_date: date | None


class FaceMatchDetail(BaseModel):
    similarity_score: float | None
    # No "reason" column is actually persisted by face_match_activity -- only the score is
    # stored (see services/pipeline/workflows/activities.py); synthesized here rather than
    # inventing a database column for a string that already has an unambiguous meaning
    # (similarity_score is null) once you know the one reason that produces it.
    reason: str | None


class SanctionsHitDetail(BaseModel):
    list_source: str
    matched_name: str
    match_score: float
    method: Literal["vector", "phonetic"]


class ReviewCaseDetailResponse(BaseModel):
    case_id: str
    status: CaseStatus
    subject_name: str
    subject_dob: date | None
    decision: CaseDecision | None
    created_at: datetime
    risk_score: float | None
    id_document_url: str | None
    selfie_url: str | None
    extraction: ExtractionDetail | None
    face_match: FaceMatchDetail | None
    sanctions_hits: list[SanctionsHitDetail]


class ReviewDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "escalated"]
    justification: str


class ReviewDecisionResponse(BaseModel):
    case_id: str
    decision: str
    reviewer_id: str
    decided_at: datetime


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreateRequest(BaseModel):
    name: str


class ApiKeyCreateResponse(ApiKeyResponse):
    # Shown exactly once, at creation -- never retrievable again, same as
    # scripts/seed_dev_tenant.py's existing raw-key semantics.
    raw_key: str


class ReviewerResponse(BaseModel):
    id: str
    email: str
    role: Literal["reviewer", "admin", "auditor"]
    created_at: datetime
    revoked_at: datetime | None


class ReviewerCreateRequest(BaseModel):
    email: str
    role: Literal["reviewer", "admin", "auditor"] = "reviewer"


class ReviewerCreateResponse(ReviewerResponse):
    raw_token: str


class WebhookResponse(BaseModel):
    id: str
    url: str
    created_at: datetime
    disabled_at: datetime | None


class WebhookCreateRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def must_be_https(cls, v: str) -> str:
        # Enforced server-side, not just as a frontend UX nicety -- webhook payloads carry case
        # decisions, and this is the one place a client can't be trusted to have applied the
        # same check the UI does.
        if not v.startswith("https://"):
            raise ValueError("webhook url must use https")
        return v


class WebhookCreateResponse(WebhookResponse):
    # Shown once, same reveal-then-never-again semantics as an API key or reviewer token --
    # the tenant needs this to verify the HMAC-SHA256 signature deliver_webhooks_activity sends
    # with every delivery.
    secret: str


class WebhookDeliveryResponse(BaseModel):
    id: str
    webhook_id: str
    case_id: str
    event_type: str
    status: Literal["pending", "delivered", "failed"]
    attempt_count: int
    last_attempted_at: datetime | None


class DashboardCaseVolumePoint(BaseModel):
    date: date
    count: int


class DashboardActivityItem(BaseModel):
    case_id: str
    subject_name: str
    event: str
    at: datetime


class DashboardSummaryResponse(BaseModel):
    status_counts: dict[str, int]
    cases_last_30_days: list[DashboardCaseVolumePoint]
    recent_activity: list[DashboardActivityItem]
