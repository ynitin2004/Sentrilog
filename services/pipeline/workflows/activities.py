import asyncio
from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError

from .. import db, ocr, storage
from ..config import settings
from ..extraction.extract import extract_id_document
from ..extraction.vlm import GeminiVLMClient


@dataclass
class FetchDocumentInput:
    tenant_id: str
    case_id: str


@dataclass
class DocumentRef:
    document_id: str
    s3_key: str


@dataclass
class ExtractDocumentInput:
    tenant_id: str
    case_id: str
    document_id: str
    s3_key: str


@dataclass
class ExtractDocumentOutput:
    needs_review: bool
    confidence: float
    method: str
    reason: str | None


@dataclass
class UpdateCaseStatusInput:
    tenant_id: str
    case_id: str
    status: str


_vlm_client = GeminiVLMClient(api_key=settings.gemini_api_key, model=settings.gemini_model)


@activity.defn
async def fetch_id_document_activity(input: FetchDocumentInput) -> DocumentRef | None:
    async with db.tenant_connection(input.tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, s3_key FROM documents WHERE case_id = $1 AND doc_type = 'id_document'",
            input.case_id,
        )
    if row is None:
        return None
    return DocumentRef(document_id=str(row["id"]), s3_key=row["s3_key"])


@activity.defn
async def extract_document_activity(input: ExtractDocumentInput) -> ExtractDocumentOutput:
    # boto3 (sync) and EasyOCR (sync, CPU-bound) would otherwise block the worker's event
    # loop -- to_thread keeps this activity truly async without needing a separate
    # activity_executor configured on the Worker (that's only required for `def`, not
    # `async def`, activities).
    image_bytes = await asyncio.to_thread(storage.get_object_bytes, input.s3_key)
    if image_bytes is None:
        # Retryable: the client may simply not have finished uploading yet. Temporal's own
        # retry policy (configured by the workflow, not here) handles the backoff/timeout --
        # this activity's job is only to report "not ready," not to decide how long to wait.
        raise ApplicationError(f"document not yet uploaded: {input.s3_key}", non_retryable=False)

    mrz_lines = await asyncio.to_thread(ocr.read_mrz_lines, image_bytes)

    result = await asyncio.to_thread(
        extract_id_document,
        _vlm_client,
        image_bytes,
        "image/jpeg",
        mrz_lines=mrz_lines,
        max_retries=settings.extraction_max_retries,
    )

    async with db.tenant_connection(input.tenant_id) as conn:
        await conn.execute(
            "INSERT INTO extractions (tenant_id, case_id, document_id, model_version, "
            "raw_json, confidence, valid) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            input.tenant_id,
            input.case_id,
            input.document_id,
            settings.gemini_model if result.method == "vlm" else "mrz-icao9303",
            result.document.model_dump_json() if result.document else "{}",
            result.confidence,
            not result.needs_review,
        )

    return ExtractDocumentOutput(
        needs_review=result.needs_review,
        confidence=result.confidence,
        method=result.method,
        reason=result.reason,
    )


@activity.defn
async def update_case_status_activity(input: UpdateCaseStatusInput) -> None:
    async with db.tenant_connection(input.tenant_id) as conn:
        await conn.execute(
            "UPDATE cases SET status = $1 WHERE id = $2", input.status, input.case_id
        )
