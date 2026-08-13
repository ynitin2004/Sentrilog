from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


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
    status: str
    id_document: UploadTarget
    selfie: UploadTarget


class CaseResponse(BaseModel):
    case_id: str
    status: str
    subject_name: str
    subject_dob: date | None
    decision: str | None
    created_at: datetime


class ReviewQueueCase(BaseModel):
    case_id: str
    subject_name: str
    subject_dob: date | None
    risk_score: float | None
    created_at: datetime
    claimed_by_reviewer_id: str | None
    claimed_at: datetime | None


class ReviewDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "escalated"]
    justification: str


class ReviewDecisionResponse(BaseModel):
    case_id: str
    decision: str
    reviewer_id: str
    decided_at: datetime
