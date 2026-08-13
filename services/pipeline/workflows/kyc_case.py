"""The kyc_case workflow: fetches the case's documents, extracts structured fields from the ID
(MRZ-first, VLM fallback -- Phase 4), then runs face match and sanctions screening concurrently
(Phase 6) -- they're independent evidence sources feeding one risk score (Phase 7), so running
them sequentially would only add latency. Retry policy at the Temporal level throughout, not
just extract.py's own bounded VLM retry -- these operate at different layers: VLM retry handles
"the model got it wrong," Temporal's activity retry handles "the worker died," "the network
blipped," or "the client hasn't finished uploading yet."

Phase 7: the review queue is `cases WHERE status = 'needs_review'`, not a separate table (see
PLAN.md) -- which means every path that parks a case in that state, not just the risk-scored
one, must keep this workflow alive awaiting a reviewer's decision signal. A workflow that
returned immediately on "no id_document" or "extraction failed" the way Phase 6 did would leave
nothing for services/intake's decision endpoint to signal once a reviewer actually looks at it.
_park_for_review_and_finalize is the one path every needs_review case now goes through.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .contracts import (
    SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
    KycCaseInput,
    KycCaseResult,
    ReviewDecisionSignal,
)

# Activities import boto3/easyocr/google-genai/insightface at module level, which the workflow
# sandbox would otherwise try to restrict/re-import on every workflow task --
# imports_passed_through tells it these are only being imported here to reference activity
# functions by name for workflow.execute_activity(), not executed as part of the workflow's
# own deterministic logic.
with workflow.unsafe.imports_passed_through():
    from .activities import (
        CaseDocuments,
        DeliverWebhooksInput,
        ExtractDocumentInput,
        ExtractDocumentOutput,
        FaceMatchInput,
        FaceMatchOutput,
        FetchDocumentInput,
        FinalizeCaseInput,
        RiskScoreInput,
        RiskScoreOutput,
        SanctionsScreenInput,
        SanctionsScreenOutput,
        UpdateCaseStatusInput,
        deliver_webhooks_activity,
        extract_document_activity,
        face_match_activity,
        fetch_case_documents_activity,
        finalize_case_activity,
        risk_score_activity,
        sanctions_screen_activity,
        update_case_status_activity,
    )


_FETCH_TIMEOUT = timedelta(seconds=30)
_WEBHOOK_TIMEOUT = timedelta(seconds=30)
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
    def __init__(self) -> None:
        # A queue, not a single overwritable slot: the signal handler can fire before the main
        # run() coroutine ever reaches _await_review_decision() (e.g. while an activity earlier
        # in the workflow is still in flight) -- a single slot that _await_review_decision()
        # resets to None on entry would silently discard a decision that arrived early. Found
        # via real Temporal server tests timing out (30s) rather than by inspection: signaling
        # immediately once the case reached needs_review only failed intermittently, exactly
        # the signature of a race rather than a deterministic bug.
        self._pending_decisions: list[ReviewDecisionSignal] = []

    @workflow.signal(name=SUBMIT_REVIEW_DECISION_SIGNAL_NAME)
    def submit_review_decision(self, signal: ReviewDecisionSignal) -> None:
        self._pending_decisions.append(signal)

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
            return await self._park_for_review_and_finalize(
                params, reason="failed to fetch case documents"
            )

        if documents.id_document is None:
            return await self._park_for_review_and_finalize(
                params, reason="no id_document found for case"
            )

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
            return await self._park_for_review_and_finalize(
                params, reason="extraction failed or timed out"
            )

        if extraction.needs_review:
            return await self._park_for_review_and_finalize(
                params,
                reason=extraction.reason,
                confidence=extraction.confidence,
                method=extraction.method,
            )

        if documents.selfie is None:
            return await self._park_for_review_and_finalize(
                params,
                reason="no selfie found for case",
                confidence=extraction.confidence,
                method=extraction.method,
            )

        # face_match and sanctions_screen are independent evidence sources feeding one risk
        # score -- start_activity schedules both immediately, without waiting for either to
        # finish, unlike execute_activity awaited one at a time.
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

        # An activity that hard-failed is a stronger, more specific signal than anything
        # risk_score_activity can express on its own (e.g. it has no "sanctions screen never
        # ran" state distinct from "0 hits") -- surfaced as its own reason rather than folded
        # silently into the score.
        if face_result is None:
            hard_failure_reason: str | None = "face match activity failed"
        elif sanctions_result is None:
            hard_failure_reason = "sanctions screening activity failed"
        else:
            hard_failure_reason = None

        risk_result: RiskScoreOutput = await workflow.execute_activity(
            risk_score_activity,
            RiskScoreInput(
                tenant_id=params.tenant_id,
                case_id=params.case_id,
                extraction_confidence=extraction.confidence,
                face_match_score=face_result.similarity_score if face_result else None,
                sanctions_hit_count=sanctions_result.hit_count if sanctions_result else 0,
            ),
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_SHORT_RETRY_POLICY,
        )

        face_match_score = face_result.similarity_score if face_result else None
        sanctions_hit_count = sanctions_result.hit_count if sanctions_result else None

        if hard_failure_reason is not None or risk_result.needs_review:
            return await self._park_for_review_and_finalize(
                params,
                reason=hard_failure_reason or risk_result.reason,
                confidence=extraction.confidence,
                method=extraction.method,
                face_match_score=face_match_score,
                sanctions_hit_count=sanctions_hit_count,
                risk_score=risk_result.risk_score,
            )

        await workflow.execute_activity(
            finalize_case_activity,
            FinalizeCaseInput(
                tenant_id=params.tenant_id,
                case_id=params.case_id,
                status="approved",
                decision="approved",
            ),
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_SHORT_RETRY_POLICY,
        )
        await workflow.execute_activity(
            deliver_webhooks_activity,
            DeliverWebhooksInput(
                tenant_id=params.tenant_id,
                case_id=params.case_id,
                event_type="case.decided",
                decision="approved",
                risk_score=risk_result.risk_score,
            ),
            start_to_close_timeout=_WEBHOOK_TIMEOUT,
            retry_policy=_SHORT_RETRY_POLICY,
        )

        return KycCaseResult(
            status="approved",
            confidence=extraction.confidence,
            method=extraction.method,
            face_match_score=face_match_score,
            sanctions_hit_count=sanctions_hit_count,
            risk_score=risk_result.risk_score,
        )

    async def _park_for_review_and_finalize(
        self,
        params: KycCaseInput,
        *,
        reason: str | None,
        confidence: float | None = None,
        method: str | None = None,
        face_match_score: float | None = None,
        sanctions_hit_count: int | None = None,
        risk_score: float | None = None,
    ) -> KycCaseResult:
        await workflow.execute_activity(
            update_case_status_activity,
            UpdateCaseStatusInput(
                tenant_id=params.tenant_id, case_id=params.case_id, status="needs_review"
            ),
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_SHORT_RETRY_POLICY,
        )

        final_decision = await self._await_review_decision()

        await workflow.execute_activity(
            finalize_case_activity,
            FinalizeCaseInput(
                tenant_id=params.tenant_id,
                case_id=params.case_id,
                status=final_decision.decision,
                decision=final_decision.decision,
            ),
            start_to_close_timeout=_FETCH_TIMEOUT,
            retry_policy=_SHORT_RETRY_POLICY,
        )
        await workflow.execute_activity(
            deliver_webhooks_activity,
            DeliverWebhooksInput(
                tenant_id=params.tenant_id,
                case_id=params.case_id,
                event_type="case.decided",
                decision=final_decision.decision,
                risk_score=risk_score,
            ),
            start_to_close_timeout=_WEBHOOK_TIMEOUT,
            retry_policy=_SHORT_RETRY_POLICY,
        )

        return KycCaseResult(
            status=final_decision.decision,
            reason=reason,
            confidence=confidence,
            method=method,
            face_match_score=face_match_score,
            sanctions_hit_count=sanctions_hit_count,
            risk_score=risk_score,
        )

    async def _await_review_decision(self) -> ReviewDecisionSignal:
        # 'escalated' isn't terminal (see PLAN.md) -- pop it off and keep waiting for a
        # follow-up decision rather than treating it as a final answer. Popping from the front
        # of the queue (not resetting a single slot) means a decision that arrived before this
        # method was even called is still there waiting, not lost.
        while True:
            await workflow.wait_condition(lambda: len(self._pending_decisions) > 0)
            signal = self._pending_decisions.pop(0)
            if signal.decision == "escalated":
                continue
            return signal
