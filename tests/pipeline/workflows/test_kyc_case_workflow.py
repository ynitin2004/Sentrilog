"""Workflow control-flow tests against the real dev Temporal server (already running via
docker-compose, same as Phase 3/4/5's tests use real infra rather than mocks). Activities are
swapped for fakes registered under the real activity names -- Temporal matches activities by
name, not Python object identity, so KycCaseWorkflow's own workflow.execute_activity/
start_activity calls transparently run whichever implementation the test's Worker registers.
This isolates the workflow's control flow from the real activities' own implementation, which
is covered separately (extraction: Phase 4/5 tests; face match: tests/pipeline/test_face_match.py
against real InsightFace; sanctions: tests/screening/ against real Qdrant/Gemini; risk scoring:
tests/pipeline/test_risk_scoring.py).

Each test uses its own task queue and workflow ID (uuid4) so concurrent test runs, and the
real dev worker if it happens to be running, never collide.

Phase 7: every needs_review path now keeps the workflow alive awaiting a
submit_review_decision signal rather than returning immediately (see kyc_case.py's module
docstring for why) -- tests that reach needs_review must signal a decision before awaiting the
result, or the workflow simply never completes within the test's execution_timeout.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.worker import Worker

from services.pipeline.config import settings
from services.pipeline.workflows.activities import (
    CaseDocuments,
    DeliverWebhooksInput,
    DeliverWebhooksOutput,
    DocumentRef,
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
)
from services.pipeline.workflows.contracts import (
    SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
    KycCaseInput,
    KycCaseResult,
    ReviewDecisionSignal,
)
from services.pipeline.workflows.kyc_case import KycCaseWorkflow

_BOTH_DOCS = CaseDocuments(
    id_document=DocumentRef(document_id="doc-id", s3_key="tenant/case/id_document"),
    selfie=DocumentRef(document_id="doc-selfie", s3_key="tenant/case/selfie"),
)
_ONLY_ID_DOC = CaseDocuments(
    id_document=DocumentRef(document_id="doc-id", s3_key="tenant/case/id_document"), selfie=None
)
_CLEAN_EXTRACTION = ExtractDocumentOutput(
    needs_review=False, confidence=0.99, method="mrz", reason=None, full_name="Jane Doe"
)
_CLEAN_FACE_MATCH = FaceMatchOutput(similarity_score=0.95, needs_review=False, reason=None)
_NO_SANCTIONS_HITS = SanctionsScreenOutput(hit_count=0, highest_score=None)


@pytest.fixture
async def temporal_client() -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


def _standard_activities(
    status_updates: list[str] | None = None,
    finalizations: list[FinalizeCaseInput] | None = None,
    webhook_deliveries: list[DeliverWebhooksInput] | None = None,
) -> list[Any]:
    """The activities every test needs regardless of which path it exercises: status updates,
    risk scoring (real logic, no DB), finalize, and webhook delivery (faked -- no real DB/HTTP
    in these control-flow tests, matching how face_match/sanctions/extract are already faked
    here)."""

    @activity.defn(name="update_case_status_activity")
    async def fake_update_status(input: UpdateCaseStatusInput) -> None:
        if status_updates is not None:
            status_updates.append(input.status)

    @activity.defn(name="risk_score_activity")
    async def fake_risk_score(input: RiskScoreInput) -> RiskScoreOutput:
        from services.pipeline.risk_scoring import RiskInputs, assess_risk

        assessment = assess_risk(
            RiskInputs(
                extraction_confidence=input.extraction_confidence,
                face_match_score=input.face_match_score,
                sanctions_hit_count=input.sanctions_hit_count,
            )
        )
        return RiskScoreOutput(
            risk_score=assessment.risk_score,
            needs_review=assessment.needs_review,
            reason=assessment.reason,
        )

    @activity.defn(name="finalize_case_activity")
    async def fake_finalize(input: FinalizeCaseInput) -> None:
        if finalizations is not None:
            finalizations.append(input)

    @activity.defn(name="deliver_webhooks_activity")
    async def fake_deliver_webhooks(input: DeliverWebhooksInput) -> DeliverWebhooksOutput:
        if webhook_deliveries is not None:
            webhook_deliveries.append(input)
        return DeliverWebhooksOutput(delivered_count=0, failed_count=0)

    return [fake_update_status, fake_risk_score, fake_finalize, fake_deliver_webhooks]


def _fetch(documents: CaseDocuments):  # type: ignore[no-untyped-def]
    @activity.defn(name="fetch_case_documents_activity")
    async def fake_fetch(input: FetchDocumentInput) -> CaseDocuments:
        return documents

    return fake_fetch


def _extract(output: ExtractDocumentOutput):  # type: ignore[no-untyped-def]
    @activity.defn(name="extract_document_activity")
    async def fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
        return output

    return fake_extract


def _face_match(output: FaceMatchOutput):  # type: ignore[no-untyped-def]
    @activity.defn(name="face_match_activity")
    async def fake_face_match(input: FaceMatchInput) -> FaceMatchOutput:
        return output

    return fake_face_match


def _sanctions(output: SanctionsScreenOutput):  # type: ignore[no-untyped-def]
    @activity.defn(name="sanctions_screen_activity")
    async def fake_sanctions(input: SanctionsScreenInput) -> SanctionsScreenOutput:
        return output

    return fake_sanctions


async def _start(client: Client, task_queue: str, params: KycCaseInput):  # type: ignore[no-untyped-def]
    return await client.start_workflow(
        KycCaseWorkflow.run,
        params,
        id=f"test-kyc-case-{uuid.uuid4()}",
        task_queue=task_queue,
        execution_timeout=timedelta(seconds=30),
    )


async def _wait_until_parked(status_updates: list[str]) -> None:
    for _ in range(100):
        if "needs_review" in status_updates:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("workflow never reached needs_review before signaling")


async def _run_to_needs_review_then_decide(
    client: Client,
    task_queue: str,
    params: KycCaseInput,
    decision: str,
    status_updates: list[str],
    justification: str = "ok",
) -> KycCaseResult:
    """Starts the workflow, waits until it has actually parked (status update observed, via the
    status_updates sink the caller registered on its Worker) so the signal isn't sent before the
    workflow reaches its park-and-wait point, then signals a decision and awaits the result."""
    handle = await _start(client, task_queue, params)
    await _wait_until_parked(status_updates)
    await handle.signal(
        SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
        ReviewDecisionSignal(reviewer_id="rev-1", decision=decision, justification=justification),
    )
    result: KycCaseResult = await handle.result()
    return result


async def test_no_id_document_parks_then_reviewer_rejects(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []
    finalizations: list[FinalizeCaseInput] = []

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(CaseDocuments(id_document=None, selfie=None)),
            *_standard_activities(status_updates=status_updates, finalizations=finalizations),
        ],
    ):
        handle = await _start(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )
        for _ in range(100):
            if status_updates:
                break
            await asyncio.sleep(0.05)
        await handle.signal(
            SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
            ReviewDecisionSignal(reviewer_id="rev-1", decision="rejected", justification="no id"),
        )
        result: KycCaseResult = await handle.result()

    assert status_updates == ["needs_review"]
    assert result.status == "rejected"
    assert result.reason == "no id_document found for case"
    assert finalizations == [
        FinalizeCaseInput(tenant_id="t1", case_id="c1", status="rejected", decision="rejected")
    ]


async def test_fetch_document_hard_failure_parks_not_crash(temporal_client: Client) -> None:
    """Regression test for a real bug found via the manual worker-restart resilience test
    (Phase 5): fetch_case_documents_activity's failure was originally uncaught, so exhausting
    its retries crashed the entire workflow (WorkflowFailureError) instead of resolving to
    needs_review like every other failure mode in this workflow."""
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []

    @activity.defn(name="fetch_case_documents_activity")
    async def fake_fetch(input: FetchDocumentInput) -> CaseDocuments:
        raise ApplicationError("simulated hard failure", non_retryable=True)

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[fake_fetch, *_standard_activities(status_updates=status_updates)],
    ):
        result = await _run_to_needs_review_then_decide(
            temporal_client,
            task_queue,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            "approved",
            status_updates,
        )

    assert result.status == "approved"
    assert result.reason == "failed to fetch case documents"


async def test_missing_selfie_parks_without_running_downstream(temporal_client: Client) -> None:
    """A successful extraction with no selfie on file must not attempt face match at all."""
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []
    face_match_calls = []

    @activity.defn(name="face_match_activity")
    async def fake_face_match(input: FaceMatchInput) -> FaceMatchOutput:
        face_match_calls.append(input)
        return _CLEAN_FACE_MATCH

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_ONLY_ID_DOC),
            _extract(_CLEAN_EXTRACTION),
            fake_face_match,
            *_standard_activities(status_updates=status_updates),
        ],
    ):
        result = await _run_to_needs_review_then_decide(
            temporal_client,
            task_queue,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            "approved",
            status_updates,
        )

    assert result.status == "approved"
    assert result.reason == "no selfie found for case"
    assert face_match_calls == []


async def test_successful_full_flow_auto_approves_without_a_reviewer(
    temporal_client: Client,
) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    finalizations: list[FinalizeCaseInput] = []
    webhook_deliveries: list[DeliverWebhooksInput] = []

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            _extract(_CLEAN_EXTRACTION),
            _face_match(_CLEAN_FACE_MATCH),
            _sanctions(_NO_SANCTIONS_HITS),
            *_standard_activities(
                finalizations=finalizations, webhook_deliveries=webhook_deliveries
            ),
        ],
    ):
        result: KycCaseResult = await temporal_client.execute_workflow(
            KycCaseWorkflow.run,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            id=f"test-kyc-case-{uuid.uuid4()}",
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=30),
        )

    assert result.status == "approved"
    assert result.confidence == 0.99
    assert result.method == "mrz"
    assert result.face_match_score == 0.95
    assert result.sanctions_hit_count == 0
    assert result.risk_score is not None and result.risk_score < 0.15
    assert finalizations == [
        FinalizeCaseInput(tenant_id="t1", case_id="c1", status="approved", decision="approved")
    ]
    assert len(webhook_deliveries) == 1
    assert webhook_deliveries[0].decision == "approved"


async def test_low_confidence_extraction_short_circuits_before_face_and_sanctions(
    temporal_client: Client,
) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    downstream_calls = []

    @activity.defn(name="face_match_activity")
    async def fake_face_match(input: FaceMatchInput) -> FaceMatchOutput:
        downstream_calls.append("face_match")
        return _CLEAN_FACE_MATCH

    @activity.defn(name="sanctions_screen_activity")
    async def fake_sanctions(input: SanctionsScreenInput) -> SanctionsScreenOutput:
        downstream_calls.append("sanctions")
        return _NO_SANCTIONS_HITS

    failed_extraction = ExtractDocumentOutput(
        needs_review=True, confidence=0.0, method="vlm", reason="exhausted retries", full_name=None
    )
    status_updates: list[str] = []

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            _extract(failed_extraction),
            fake_face_match,
            fake_sanctions,
            *_standard_activities(status_updates=status_updates),
        ],
    ):
        result = await _run_to_needs_review_then_decide(
            temporal_client,
            task_queue,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            "rejected",
            status_updates,
        )

    assert result.status == "rejected"
    assert result.reason == "exhausted retries"
    assert downstream_calls == []  # face match / sanctions screening never ran


async def test_extraction_activity_hard_failure_parks_case(temporal_client: Client) -> None:
    """The activity itself erroring out (not just returning needs_review=True) must still park
    the case for review, not leave it hanging or crash the workflow -- exercised with a
    non-retryable error so the test doesn't wait through the real 20-minute retry budget."""
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []

    @activity.defn(name="extract_document_activity")
    async def fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
        raise ApplicationError("simulated hard failure", non_retryable=True)

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            fake_extract,
            *_standard_activities(status_updates=status_updates),
        ],
    ):
        result = await _run_to_needs_review_then_decide(
            temporal_client,
            task_queue,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            "approved",
            status_updates,
        )

    assert result.status == "approved"
    assert result.reason == "extraction failed or timed out"


async def test_no_face_detected_parks_case(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []
    no_face = FaceMatchOutput(similarity_score=None, needs_review=True, reason="no face detected")

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            _extract(_CLEAN_EXTRACTION),
            _face_match(no_face),
            _sanctions(_NO_SANCTIONS_HITS),
            *_standard_activities(status_updates=status_updates),
        ],
    ):
        result = await _run_to_needs_review_then_decide(
            temporal_client,
            task_queue,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            "rejected",
            status_updates,
        )

    assert result.status == "rejected"
    assert result.face_match_score is None
    assert result.reason == "no face detected for comparison"


async def test_sanctions_hit_parks_case(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []
    a_hit = SanctionsScreenOutput(hit_count=1, highest_score=0.93)

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            _extract(_CLEAN_EXTRACTION),
            _face_match(_CLEAN_FACE_MATCH),
            _sanctions(a_hit),
            *_standard_activities(status_updates=status_updates),
        ],
    ):
        result = await _run_to_needs_review_then_decide(
            temporal_client,
            task_queue,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            "rejected",
            status_updates,
        )

    assert result.status == "rejected"
    assert result.sanctions_hit_count == 1
    assert result.reason == "sanctions list hit"


async def test_low_risk_score_without_hard_flags_parks_case(temporal_client: Client) -> None:
    """A case with no sanctions hit and a detected face, but low enough combined confidence to
    fail risk_scoring's auto-clear threshold, must still be routed to review -- this is the
    scenario risk scoring adds on top of Phase 6's simpler any-red-flag gate."""
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []
    borderline_extraction = ExtractDocumentOutput(
        needs_review=False, confidence=0.5, method="vlm", reason=None, full_name="Jane Doe"
    )
    borderline_face_match = FaceMatchOutput(similarity_score=0.5, needs_review=False, reason=None)

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            _extract(borderline_extraction),
            _face_match(borderline_face_match),
            _sanctions(_NO_SANCTIONS_HITS),
            *_standard_activities(status_updates=status_updates),
        ],
    ):
        result = await _run_to_needs_review_then_decide(
            temporal_client,
            task_queue,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            "approved",
            status_updates,
        )

    assert result.status == "approved"  # the reviewer's call, not an auto-decision
    assert result.reason is not None and "below auto-clear threshold" in result.reason


async def test_escalated_decision_is_not_terminal_workflow_waits_for_a_follow_up(
    temporal_client: Client,
) -> None:
    """PLAN.md's Phase 7 design decision: 'escalated' parks the case again rather than ending
    the workflow, so a second reviewer (or the same one, after consulting someone) can still
    submit a real decision."""
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(CaseDocuments(id_document=None, selfie=None)),
            *_standard_activities(status_updates=status_updates),
        ],
    ):
        handle = await _start(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )
        for _ in range(100):
            if status_updates:
                break
            await asyncio.sleep(0.05)

        await handle.signal(
            SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
            ReviewDecisionSignal(
                reviewer_id="rev-1", decision="escalated", justification="need a second opinion"
            ),
        )
        # The workflow must still be running -- an escalation must not have completed it.
        await asyncio.sleep(0.3)
        description = await handle.describe()
        assert description.status.name == "RUNNING"

        await handle.signal(
            SUBMIT_REVIEW_DECISION_SIGNAL_NAME,
            ReviewDecisionSignal(reviewer_id="rev-2", decision="approved", justification="ok"),
        )
        result: KycCaseResult = await handle.result()

    assert result.status == "approved"


async def test_workflow_id_is_deterministic_per_case_so_restarts_dont_duplicate(
    temporal_client: Client,
) -> None:
    """Not exercised via the real starter here (that needs the intake API's DB), but proves
    the underlying Temporal guarantee this phase's exit criteria depends on: starting a
    workflow twice with the same ID against a still-running execution fails with
    WorkflowAlreadyStartedError, which services/intake/temporal.py already treats as a no-op.
    """
    task_queue = f"test-{uuid.uuid4()}"
    workflow_id = f"test-dup-{uuid.uuid4()}"

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(CaseDocuments(id_document=None, selfie=None)),
            *_standard_activities(),
        ],
    ):
        await temporal_client.start_workflow(
            KycCaseWorkflow.run,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            id=workflow_id,
            task_queue=task_queue,
        )
        with pytest.raises(WorkflowAlreadyStartedError):
            await temporal_client.start_workflow(
                KycCaseWorkflow.run,
                KycCaseInput(tenant_id="t1", case_id="c1"),
                id=workflow_id,
                task_queue=task_queue,
            )


async def test_face_match_and_sanctions_screen_run_concurrently_not_sequentially(
    temporal_client: Client,
) -> None:
    """Phase 6's stated exit criteria: face match and sanctions screening execute
    concurrently, not one after the other. Proven via the real workflow's execution history
    (activity start/complete timestamps from the actual Temporal server), which is exact and
    automatable -- not by eyeballing the Temporal UI timeline, which is what the exit criteria
    literally says but isn't something a non-interactive test can do.
    """
    task_queue = f"test-{uuid.uuid4()}"
    delay = 0.5  # wide enough that any overlap found can't be a scheduling coincidence

    @activity.defn(name="face_match_activity")
    async def slow_face_match(input: FaceMatchInput) -> FaceMatchOutput:
        await asyncio.sleep(delay)
        return _CLEAN_FACE_MATCH

    @activity.defn(name="sanctions_screen_activity")
    async def slow_sanctions(input: SanctionsScreenInput) -> SanctionsScreenOutput:
        await asyncio.sleep(delay)
        return _NO_SANCTIONS_HITS

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            _extract(_CLEAN_EXTRACTION),
            slow_face_match,
            slow_sanctions,
            *_standard_activities(),
        ],
    ):
        handle = await temporal_client.start_workflow(
            KycCaseWorkflow.run,
            KycCaseInput(tenant_id="t1", case_id="c1"),
            id=f"test-kyc-case-{uuid.uuid4()}",
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=30),
        )
        await handle.result()
        history = await handle.fetch_history()

    # Walk the real event history and reconstruct each activity's [started, completed] window
    # by following scheduled_event_id back-references -- the same data the Temporal UI
    # timeline is drawn from, just read programmatically instead of visually.
    tracked_names = ("face_match_activity", "sanctions_screen_activity")
    scheduled_ids: dict[int, str] = {}
    started_at: dict[str, datetime] = {}
    completed_at: dict[str, datetime] = {}
    for event in history.events:
        attr_name = event.WhichOneof("attributes")
        if attr_name == "activity_task_scheduled_event_attributes":
            scheduled_name = event.activity_task_scheduled_event_attributes.activity_type.name
            if scheduled_name in tracked_names:
                scheduled_ids[event.event_id] = scheduled_name
        elif attr_name == "activity_task_started_event_attributes":
            started_scheduled_id = event.activity_task_started_event_attributes.scheduled_event_id
            tracked = scheduled_ids.get(started_scheduled_id)
            if tracked:
                started_at[tracked] = event.event_time.ToDatetime()
        elif attr_name == "activity_task_completed_event_attributes":
            completed_scheduled_id = (
                event.activity_task_completed_event_attributes.scheduled_event_id
            )
            tracked = scheduled_ids.get(completed_scheduled_id)
            if tracked:
                completed_at[tracked] = event.event_time.ToDatetime()

    assert set(started_at) == set(tracked_names)
    assert set(completed_at) == set(tracked_names)

    # Genuine concurrency, checked both directions: each activity must have started before the
    # other finished. If they ran sequentially, one's start would be at or after the other's
    # completion.
    overlaps = (
        started_at["face_match_activity"] < completed_at["sanctions_screen_activity"]
        and started_at["sanctions_screen_activity"] < completed_at["face_match_activity"]
    )
    assert overlaps, (
        f"expected overlapping execution windows (concurrent), got "
        f"started_at={started_at}, completed_at={completed_at}"
    )
