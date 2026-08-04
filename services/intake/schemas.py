from datetime import date, datetime

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
