"""The kyc_case workflow: fetches the case's ID document, extracts structured fields from it
(MRZ-first, VLM fallback -- Phase 4), and records the outcome. Retry policy at the Temporal
level, not just extract.py's own bounded VLM retry -- these operate at different layers: VLM
retry handles "the model got it wrong," Temporal's activity retry handles "the worker died,"
"the network blipped," or "the client hasn't finished uploading yet."
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .contracts import KycCaseInput, KycCaseResult

# Activities import boto3/easyocr/google-genai at module level, which the workflow sandbox
# would otherwise try to restrict/re-import on every workflow task -- imports_passed_through
# tells it these are only being imported here to reference activity functions by name for
# workflow.execute_activity(), not executed as part of the workflow's own deterministic logic.
with workflow.unsafe.imports_passed_through():
    from .activities import (
        DocumentRef,
        ExtractDocumentInput,
        ExtractDocumentOutput,
        FetchDocumentInput,
        UpdateCaseStatusInput,
        extract_document_activity,
        fetch_id_document_activity,
        update_case_status_activity,
    )


_FETCH_TIMEOUT = timedelta(seconds=30)
# The client has a bounded window to actually finish uploading after receiving the presigned
# URL (15 minutes there, per services/intake/storage.py) -- 20 minutes here gives a little
# slack before the workflow gives up and routes the case to review rather than waiting forever.
_EXTRACTION_SCHEDULE_TO_CLOSE = timedelta(minutes=20)
_EXTRACTION_ATTEMPT_TIMEOUT = timedelta(minutes=5)
_EXTRACTION_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
)


@workflow.defn
class KycCaseWorkflow:
    @workflow.run
    async def run(self, params: KycCaseInput) -> KycCaseResult:
        try:
            doc_ref: DocumentRef | None = await workflow.execute_activity(
                fetch_id_document_activity,
                FetchDocumentInput(tenant_id=params.tenant_id, case_id=params.case_id),
                start_to_close_timeout=_FETCH_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except ActivityError:
            # Found by testing worker-restart resilience, not by inspection: this activity's
            # failure was originally uncaught, so exhausting its retries crashed the whole
            # workflow instead of resolving to needs_review like every other failure mode here.
            await self._mark_needs_review(params)
            return KycCaseResult(status="needs_review", reason="failed to fetch case documents")

        if doc_ref is None:
            await self._mark_needs_review(params)
            return KycCaseResult(status="needs_review", reason="no id_document found for case")

        try:
            extraction: ExtractDocumentOutput = await workflow.execute_activity(
                extract_document_activity,
                ExtractDocumentInput(
                    tenant_id=params.tenant_id,
                    case_id=params.case_id,
                    document_id=doc_ref.document_id,
                    s3_key=doc_ref.s3_key,
                ),
                schedule_to_close_timeout=_EXTRACTION_SCHEDULE_TO_CLOSE,
                start_to_close_timeout=_EXTRACTION_ATTEMPT_TIMEOUT,
                retry_policy=_EXTRACTION_RETRY_POLICY,
            )
        except ActivityError:
            await self._mark_needs_review(params)
            return KycCaseResult(status="needs_review", reason="extraction failed or timed out")

        new_status = "needs_review" if extraction.needs_review else "processing"
        await workflow.execute_activity(
            update_case_status_activity,
            UpdateCaseStatusInput(
                tenant_id=params.tenant_id, case_id=params.case_id, status=new_status
            ),
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return KycCaseResult(
            status=new_status,
            confidence=extraction.confidence,
            method=extraction.method,
            reason=extraction.reason,
        )

    async def _mark_needs_review(self, params: KycCaseInput) -> None:
        await workflow.execute_activity(
            update_case_status_activity,
            UpdateCaseStatusInput(
                tenant_id=params.tenant_id, case_id=params.case_id, status="needs_review"
            ),
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
