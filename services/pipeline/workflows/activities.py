import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .. import audit, db, ocr, storage
from ..config import settings
from ..extraction.extract import extract_id_document
from ..extraction.vlm import GeminiVLMClient
from ..face_match import FaceMatchClient, InsightFaceClient, NoFaceDetectedError
from ..risk_scoring import RiskInputs, assess_risk

if TYPE_CHECKING:
    # Only for the type hint on the module-level cache below -- the real import is deferred to
    # inside sanctions_screen_activity (see the comment there for why).
    from ...screening.embeddings import EmbeddingClient


@dataclass
class FetchDocumentInput:
    tenant_id: str
    case_id: str


@dataclass
class DocumentRef:
    document_id: str
    s3_key: str


@dataclass
class CaseDocuments:
    id_document: DocumentRef | None
    selfie: DocumentRef | None


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
    full_name: str | None  # needed by sanctions_screen_activity, which runs after this


@dataclass
class FaceMatchInput:
    tenant_id: str
    case_id: str
    id_document_s3_key: str
    selfie_s3_key: str


@dataclass
class FaceMatchOutput:
    similarity_score: float | None
    needs_review: bool
    reason: str | None


@dataclass
class SanctionsScreenInput:
    tenant_id: str
    case_id: str
    full_name: str


@dataclass
class SanctionsScreenOutput:
    hit_count: int
    highest_score: float | None


@dataclass
class UpdateCaseStatusInput:
    tenant_id: str
    case_id: str
    status: str


@dataclass
class RiskScoreInput:
    tenant_id: str
    case_id: str
    extraction_confidence: float
    face_match_score: float | None
    sanctions_hit_count: int


@dataclass
class RiskScoreOutput:
    risk_score: float
    needs_review: bool
    reason: str | None


@dataclass
class FinalizeCaseInput:
    tenant_id: str
    case_id: str
    status: str
    decision: str | None


@dataclass
class DeliverWebhooksInput:
    tenant_id: str
    case_id: str
    event_type: str
    decision: str | None
    risk_score: float | None


@dataclass
class DeliverWebhooksOutput:
    delivered_count: int
    failed_count: int


# Delivery attempts per registered webhook, tried synchronously inside the activity with a
# short backoff -- deliberately not relying on Temporal's own activity-level retry for this,
# since retrying the whole activity would re-deliver to webhooks that already succeeded on an
# earlier attempt (webhook_deliveries rows aren't upserted/deduplicated).
_WEBHOOK_MAX_ATTEMPTS = 3
_WEBHOOK_RETRY_BACKOFF_SECONDS = 1.0


_vlm_client = GeminiVLMClient(api_key=settings.gemini_api_key, model=settings.gemini_model)
_face_match_client: FaceMatchClient | None = None
_embedding_client: "EmbeddingClient | None" = None


def _get_face_match_client() -> FaceMatchClient:
    global _face_match_client
    if _face_match_client is None:
        _face_match_client = InsightFaceClient()
    return _face_match_client


@activity.defn
async def fetch_case_documents_activity(input: FetchDocumentInput) -> CaseDocuments:
    async with audit.audited(input.tenant_id, input.case_id, "fetch_case_documents") as a:
        async with db.tenant_connection(input.tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT id, doc_type, s3_key FROM documents WHERE case_id = $1", input.case_id
            )
        by_type = {
            r["doc_type"]: DocumentRef(document_id=str(r["id"]), s3_key=r["s3_key"]) for r in rows
        }
        a.payload = {"doc_types_found": sorted(by_type.keys())}
        return CaseDocuments(id_document=by_type.get("id_document"), selfie=by_type.get("selfie"))


@activity.defn
async def extract_document_activity(input: ExtractDocumentInput) -> ExtractDocumentOutput:
    async with audit.audited(input.tenant_id, input.case_id, "extract_document") as a:
        # boto3 (sync) and EasyOCR (sync, CPU-bound) would otherwise block the worker's event
        # loop -- to_thread keeps this activity truly async without needing a separate
        # activity_executor configured on the Worker (that's only required for `def`, not
        # `async def`, activities).
        image_bytes = await asyncio.to_thread(storage.get_object_bytes, input.s3_key)
        if image_bytes is None:
            # Retryable: the client may simply not have finished uploading yet. Temporal's own
            # retry policy (configured by the workflow, not here) handles the backoff/timeout --
            # this activity's job is only to report "not ready," not to decide how long to wait.
            raise ApplicationError(
                f"document not yet uploaded: {input.s3_key}", non_retryable=False
            )

        mrz_lines = await asyncio.to_thread(ocr.read_mrz_lines, image_bytes)

        result = await asyncio.to_thread(
            extract_id_document,
            _vlm_client,
            image_bytes,
            "image/jpeg",
            mrz_lines=mrz_lines,
            max_retries=settings.extraction_max_retries,
        )
        model_version = settings.gemini_model if result.method == "vlm" else "mrz-icao9303"

        async with db.tenant_connection(input.tenant_id) as conn:
            await conn.execute(
                "INSERT INTO extractions (tenant_id, case_id, document_id, model_version, "
                "raw_json, confidence, valid) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                input.tenant_id,
                input.case_id,
                input.document_id,
                model_version,
                result.document.model_dump_json() if result.document else "{}",
                result.confidence,
                not result.needs_review,
            )

        a.model_version = model_version
        a.input_hash = hashlib.sha256(image_bytes).hexdigest()
        a.payload = {
            "method": result.method,
            "confidence": result.confidence,
            "needs_review": result.needs_review,
        }

        return ExtractDocumentOutput(
            needs_review=result.needs_review,
            confidence=result.confidence,
            method=result.method,
            reason=result.reason,
            full_name=result.document.full_name if result.document else None,
        )


@activity.defn
async def face_match_activity(input: FaceMatchInput) -> FaceMatchOutput:
    async with audit.audited(input.tenant_id, input.case_id, "face_match") as a:
        id_photo_bytes = await asyncio.to_thread(storage.get_object_bytes, input.id_document_s3_key)
        selfie_bytes = await asyncio.to_thread(storage.get_object_bytes, input.selfie_s3_key)
        if id_photo_bytes is None or selfie_bytes is None:
            raise ApplicationError("documents not yet uploaded for face match", non_retryable=False)

        a.model_version = "insightface-buffalo_l"
        a.input_hash = hashlib.sha256(id_photo_bytes + selfie_bytes).hexdigest()

        client = _get_face_match_client()
        try:
            score = await asyncio.to_thread(client.compare_faces, id_photo_bytes, selfie_bytes)
            needs_review = False
            reason = None
        except NoFaceDetectedError as exc:
            score = None
            needs_review = True
            reason = str(exc)

        async with db.tenant_connection(input.tenant_id) as conn:
            await conn.execute(
                "INSERT INTO face_matches (tenant_id, case_id, similarity_score, model_version) "
                "VALUES ($1, $2, $3, $4)",
                input.tenant_id,
                input.case_id,
                score,
                "insightface-buffalo_l",
            )

        a.payload = {"similarity_score": score, "needs_review": needs_review, "reason": reason}
        return FaceMatchOutput(similarity_score=score, needs_review=needs_review, reason=reason)


@activity.defn
async def sanctions_screen_activity(input: SanctionsScreenInput) -> SanctionsScreenOutput:
    async with audit.audited(input.tenant_id, input.case_id, "sanctions_screen") as a:
        # Imported here, not at module level: screening has its own config.py (separate
        # GEMINI_EMBEDDING_MODEL/QDRANT_* settings) -- keeping the import local avoids pulling
        # services.screening's settings validation into every activity module import, only this
        # one.
        from ...screening.config import settings as screening_settings
        from ...screening.embeddings import EmbeddingClient, GeminiEmbeddingClient
        from ...screening.screen import screen_name

        global _embedding_client
        if _embedding_client is None:
            _embedding_client = GeminiEmbeddingClient(
                api_key=screening_settings.gemini_api_key,
                model=screening_settings.gemini_embedding_model,
                dimensions=screening_settings.embedding_dimensions,
            )
        client: EmbeddingClient = _embedding_client
        a.model_version = screening_settings.gemini_embedding_model

        hits = await asyncio.to_thread(screen_name, client, input.full_name)

        async with db.tenant_connection(input.tenant_id) as conn:
            for hit in hits:
                await conn.execute(
                    "INSERT INTO sanctions_hits (tenant_id, case_id, list_source, matched_name, "
                    "match_score, method) VALUES ($1, $2, $3, $4, $5, $6)",
                    input.tenant_id,
                    input.case_id,
                    hit.list_source,
                    hit.matched_name,
                    hit.match_score,
                    hit.method,
                )

        highest = max((h.match_score for h in hits), default=None)
        a.payload = {"hit_count": len(hits), "highest_score": highest}
        return SanctionsScreenOutput(hit_count=len(hits), highest_score=highest)


def _case_event_payload(
    tenant_id: str, case_id: str, status: str, *, decision: str | None = None
) -> str:
    payload: dict[str, str | None] = {
        "tenant_id": tenant_id,
        "case_id": case_id,
        "status": status,
        "decision": decision,
    }
    return json.dumps(payload)


@activity.defn
async def update_case_status_activity(input: UpdateCaseStatusInput) -> None:
    async with audit.audited(input.tenant_id, input.case_id, "update_case_status") as a:
        async with db.tenant_connection(input.tenant_id) as conn:
            await conn.execute(
                "UPDATE cases SET status = $1 WHERE id = $2", input.status, input.case_id
            )
            # NOTIFY is only delivered when this transaction commits (tenant_connection wraps
            # every statement in one), so a listener can never observe a status that later
            # rolled back.
            await conn.execute(
                "SELECT pg_notify('case_events', $1)",
                _case_event_payload(input.tenant_id, input.case_id, input.status),
            )
        a.payload = {"status": input.status}


@activity.defn
async def risk_score_activity(input: RiskScoreInput) -> RiskScoreOutput:
    async with audit.audited(input.tenant_id, input.case_id, "risk_score") as a:
        assessment = assess_risk(
            RiskInputs(
                extraction_confidence=input.extraction_confidence,
                face_match_score=input.face_match_score,
                sanctions_hit_count=input.sanctions_hit_count,
            )
        )
        async with db.tenant_connection(input.tenant_id) as conn:
            await conn.execute(
                "UPDATE cases SET risk_score = $1 WHERE id = $2",
                assessment.risk_score,
                input.case_id,
            )
        a.payload = {
            "risk_score": assessment.risk_score,
            "needs_review": assessment.needs_review,
            "reason": assessment.reason,
        }
        return RiskScoreOutput(
            risk_score=assessment.risk_score,
            needs_review=assessment.needs_review,
            reason=assessment.reason,
        )


@activity.defn
async def finalize_case_activity(input: FinalizeCaseInput) -> None:
    async with audit.audited(input.tenant_id, input.case_id, "finalize_case") as a:
        async with db.tenant_connection(input.tenant_id) as conn:
            await conn.execute(
                "UPDATE cases SET status = $1, decision = $2, decided_at = now() WHERE id = $3",
                input.status,
                input.decision,
                input.case_id,
            )
            await conn.execute(
                "SELECT pg_notify('case_events', $1)",
                _case_event_payload(
                    input.tenant_id, input.case_id, input.status, decision=input.decision
                ),
            )
        a.payload = {"status": input.status, "decision": input.decision}


@activity.defn
async def deliver_webhooks_activity(input: DeliverWebhooksInput) -> DeliverWebhooksOutput:
    async with audit.audited(input.tenant_id, input.case_id, "deliver_webhooks") as a:
        async with db.tenant_connection(input.tenant_id) as conn:
            webhooks = await conn.fetch(
                "SELECT id, url, secret FROM webhooks WHERE tenant_id = $1 "
                "AND disabled_at IS NULL",
                input.tenant_id,
            )

        if not webhooks:
            # Not every tenant registers a webhook -- nothing to deliver isn't a failure.
            a.payload = {"delivered_count": 0, "failed_count": 0, "webhook_count": 0}
            return DeliverWebhooksOutput(delivered_count=0, failed_count=0)

        payload_json = json.dumps(
            {
                "event": input.event_type,
                "case_id": input.case_id,
                "decision": input.decision,
                "risk_score": input.risk_score,
            },
            sort_keys=True,
        )
        body = payload_json.encode("utf-8")

        delivered_count = 0
        failed_count = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            for webhook in webhooks:
                signature = hmac.new(
                    webhook["secret"].encode("utf-8"), body, hashlib.sha256
                ).hexdigest()

                success = False
                attempts = 0
                for attempts in range(1, _WEBHOOK_MAX_ATTEMPTS + 1):
                    try:
                        response = await client.post(
                            webhook["url"],
                            content=body,
                            headers={
                                "Content-Type": "application/json",
                                "X-Sentrilog-Signature": signature,
                            },
                        )
                        response.raise_for_status()
                        success = True
                        break
                    except httpx.HTTPError:
                        if attempts < _WEBHOOK_MAX_ATTEMPTS:
                            await asyncio.sleep(_WEBHOOK_RETRY_BACKOFF_SECONDS * attempts)

                delivered_count += 1 if success else 0
                failed_count += 0 if success else 1

                async with db.tenant_connection(input.tenant_id) as conn:
                    await conn.execute(
                        "INSERT INTO webhook_deliveries (tenant_id, webhook_id, case_id, "
                        "event_type, payload, status, attempt_count, last_attempted_at) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7, now())",
                        input.tenant_id,
                        webhook["id"],
                        input.case_id,
                        input.event_type,
                        payload_json,
                        "delivered" if success else "failed",
                        attempts,
                    )

        a.payload = {
            "delivered_count": delivered_count,
            "failed_count": failed_count,
            "webhook_count": len(webhooks),
        }
        return DeliverWebhooksOutput(delivered_count=delivered_count, failed_count=failed_count)
