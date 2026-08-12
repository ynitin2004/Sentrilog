"""The kyc_case workflow: fetches the case's documents, extracts structured fields from the ID
(MRZ-first, VLM fallback -- Phase 4), then runs face match and sanctions screening concurrently
(Phase 6) -- they're independent evidence sources feeding one eventual risk score (Phase 7), so
running them sequentially would only add latency. Retry policy at the Temporal level throughout,
not just extract.py's own bounded VLM retry -- these operate at different layers: VLM retry
handles "the model got it wrong," Temporal's activity retry handles "the worker died," "the
network blipped," or "the client hasn't finished uploading yet."
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .contracts import KycCaseInput, KycCaseResult

# Activities import boto3/easyocr/google-genai/insightface at module level, which the workflow
# sandbox would otherwise try to restrict/re-import on every workflow task --
# imports_passed_through tells it these are only being imported here to reference activity
# functions by name for workflow.execute_activity(), not executed as part of the workflow's
# own deterministic logic.
with workflow.unsafe.imports_passed_through():
    from .activities import (
        CaseDocuments,
        ExtractDocumentInput,
        ExtractDocumentOutput,
        FaceMatchInput,
        FaceMatchOutput,
        FetchDocumentInput,
        SanctionsScreenInput,
        SanctionsScreenOutput,
        UpdateCaseStatusInput,
        extract_document_activity,
        face_match_activity,
        fetch_case_documents_activity,
        sanctions_screen_activity,
        update_case_status_activity,
    )


_FETCH_TIMEOUT = timedelta(seconds=30)
# The client has a bounded window to actually finish uploading after receiving the presigned
# URL (15 minutes there, per services/intake/storage.py) -- 20 minutes here gives a little
# slack before the workflow gives up and routes the case to review rather than waiting forever.
_LONG_SCHEDULE_TO_CLOSE = timedelta(minutes=20)
_LONG_ATTEMPT_TIMEOUT = timedelta(minutes=5)
_LONG_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
)
_SHORT_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


@workflow.defn
class KycCaseWorkflow:
    @workflow.run
    async def run(self, params: KycCaseInput) -> KycCaseResult:
        try:
            documents: CaseDocuments = await workflow.execute_activity(
                fetch_case_documents_activity,
                FetchDocumentInput(tenant_id=params.tenant_id, case_id=params.case_id),
                start_to_close_timeout=_FETCH_TIMEOUT,
                retry_policy=_SHORT_RETRY_POLICY,
            )
        except ActivityError:
            # Found by testing worker-restart resilience, not by inspection: this activity's
            # failure was originally uncaught, so exhausting its retries crashed the whole
            # workflow instead of resolving to needs_review like every other failure mode here.
            await self._mark_needs_review(params)
            return KycCaseResult(status="needs_review", reason="failed to fetch case documents")

        if documents.id_document is None:
            await self._mark_needs_review(params)
            return KycCaseResult(status="needs_review", reason="no id_document found for case")

        try:
            extraction: ExtractDocumentOutput = await workflow.execute_activity(
                extract_document_activity,
                ExtractDocumentInput(
                    tenant_id=params.tenant_id,
                    case_id=params.case_id,
                    document_id=documents.id_document.document_id,
                    s3_key=documents.id_document.s3_key,
                ),
                schedule_to_close_timeout=_LONG_SCHEDULE_TO_CLOSE,
                start_to_close_timeout=_LONG_ATTEMPT_TIMEOUT,
                retry_policy=_LONG_RETRY_POLICY,
            )
        except ActivityError:
            await self._mark_needs_review(params)
            return KycCaseResult(status="needs_review", reason="extraction failed or timed out")

        if extraction.needs_review:
            await self._mark_needs_review(params)
            return KycCaseResult(
                status="needs_review",
                reason=extraction.reason,
                confidence=extraction.confidence,
                method=extraction.method,
            )

        if documents.selfie is None:
            await self._mark_needs_review(params)
            return KycCaseResult(
                status="needs_review",
                reason="no selfie found for case",
                confidence=extraction.confidence,
                method=extraction.method,
            )

        # face_match and sanctions_screen are independent evidence sources feeding one future
        # risk score (Phase 7) -- start_activity schedules both immediately, without waiting
        # for either to finish, unlike execute_activity awaited one at a time.
        face_match_handle = workflow.start_activity(
            face_match_activity,
            FaceMatchInput(
                tenant_id=params.tenant_id,
                case_id=params.case_id,
                id_document_s3_key=documents.id_document.s3_key,
                selfie_s3_key=documents.selfie.s3_key,
            ),
            schedule_to_close_timeout=_LONG_SCHEDULE_TO_CLOSE,
            start_to_close_timeout=_LONG_ATTEMPT_TIMEOUT,
            retry_policy=_LONG_RETRY_POLICY,
        )
        sanctions_handle = workflow.start_activity(
            sanctions_screen_activity,
            SanctionsScreenInput(
                tenant_id=params.tenant_id,
                case_id=params.case_id,
                full_name=extraction.full_name or "",
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_SHORT_RETRY_POLICY,
        )

        try:
            face_result: FaceMatchOutput | None = await face_match_handle
        except ActivityError:
            face_result = None
        try:
            sanctions_result: SanctionsScreenOutput | None = await sanctions_handle
        except ActivityError:
            sanctions_result = None

        needs_review = (
            face_result is None
            or face_result.needs_review
            or sanctions_result is None
            or sanctions_result.hit_count > 0
        )
        new_status = "needs_review" if needs_review else "processing"

        await workflow.execute_activity(
            update_case_status_activity,
            UpdateCaseStatusInput(
                tenant_id=params.tenant_id, case_id=params.case_id, status=new_status
            ),
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_SHORT_RETRY_POLICY,
        )

        return KycCaseResult(
            status=new_status,
            confidence=extraction.confidence,
            method=extraction.method,
            face_match_score=face_result.similarity_score if face_result else None,
            sanctions_hit_count=sanctions_result.hit_count if sanctions_result else None,
        )

    async def _mark_needs_review(self, params: KycCaseInput) -> None:
        await workflow.execute_activity(
            update_case_status_activity,
            UpdateCaseStatusInput(
                tenant_id=params.tenant_id, case_id=params.case_id, status="needs_review"
            ),
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_SHORT_RETRY_POLICY,
        )
