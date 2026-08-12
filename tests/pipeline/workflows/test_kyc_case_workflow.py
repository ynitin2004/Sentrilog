"""Workflow control-flow tests against the real dev Temporal server (already running via
docker-compose, same as Phase 3/4/5's tests use real infra rather than mocks). Activities are
swapped for fakes registered under the real activity names -- Temporal matches activities by
name, not Python object identity, so KycCaseWorkflow's own workflow.execute_activity/
start_activity calls transparently run whichever implementation the test's Worker registers.
This isolates the workflow's control flow from the real activities' own implementation, which
is covered separately (extraction: Phase 4/5 tests; face match: tests/pipeline/test_face_match.py
against real InsightFace; sanctions: tests/screening/ against real Qdrant/Gemini).

Each test uses its own task queue and workflow ID (uuid4) so concurrent test runs, and the
real dev worker if it happens to be running, never collide.
"""

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.worker import Worker

from services.pipeline.config import settings
from services.pipeline.workflows.activities import (
    CaseDocuments,
    DocumentRef,
    ExtractDocumentInput,
    ExtractDocumentOutput,
    FaceMatchInput,
    FaceMatchOutput,
    FetchDocumentInput,
    SanctionsScreenInput,
    SanctionsScreenOutput,
    UpdateCaseStatusInput,
)
from services.pipeline.workflows.contracts import KycCaseInput, KycCaseResult
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


async def _run_workflow(client: Client, task_queue: str, params: KycCaseInput) -> KycCaseResult:
    return await client.execute_workflow(
        KycCaseWorkflow.run,
        params,
        id=f"test-kyc-case-{uuid.uuid4()}",
        task_queue=task_queue,
        execution_timeout=timedelta(seconds=30),
    )


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


def _update_status(sink: list[str]):  # type: ignore[no-untyped-def]
    @activity.defn(name="update_case_status_activity")
    async def fake_update_status(input: UpdateCaseStatusInput) -> None:
        sink.append(input.status)

    return fake_update_status


async def test_no_id_document_marks_case_needs_review(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(CaseDocuments(id_document=None, selfie=None)),
            _update_status(status_updates),
        ],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.reason == "no id_document found for case"
    assert status_updates == ["needs_review"]


async def test_fetch_document_hard_failure_marks_case_needs_review_not_crash(
    temporal_client: Client,
) -> None:
    """Regression test for a real bug found via the manual worker-restart resilience test
    (Phase 5): fetch_case_documents_activity's failure was originally uncaught, so exhausting
    its retries crashed the entire workflow (WorkflowFailureError) instead of resolving to
    needs_review like every other failure mode in this workflow."""
    task_queue = f"test-{uuid.uuid4()}"

    @activity.defn(name="fetch_case_documents_activity")
    async def fake_fetch(input: FetchDocumentInput) -> CaseDocuments:
        raise ApplicationError("simulated hard failure", non_retryable=True)

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[fake_fetch, _update_status([])],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.reason == "failed to fetch case documents"


async def test_missing_selfie_marks_case_needs_review_without_running_downstream(
    temporal_client: Client,
) -> None:
    """A successful extraction with no selfie on file must not attempt face match at all."""
    task_queue = f"test-{uuid.uuid4()}"
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
            _update_status([]),
        ],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.reason == "no selfie found for case"
    assert face_match_calls == []


async def test_successful_full_flow_marks_case_processing(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            _extract(_CLEAN_EXTRACTION),
            _face_match(_CLEAN_FACE_MATCH),
            _sanctions(_NO_SANCTIONS_HITS),
            _update_status(status_updates),
        ],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "processing"
    assert result.confidence == 0.99
    assert result.method == "mrz"
    assert result.face_match_score == 0.95
    assert result.sanctions_hit_count == 0
    assert status_updates == ["processing"]


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

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[
            _fetch(_BOTH_DOCS),
            _extract(failed_extraction),
            fake_face_match,
            fake_sanctions,
            _update_status([]),
        ],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.reason == "exhausted retries"
    assert downstream_calls == []  # face match / sanctions screening never ran


async def test_extraction_activity_hard_failure_marks_case_needs_review(
    temporal_client: Client,
) -> None:
    """The activity itself erroring out (not just returning needs_review=True) must still
    resolve the workflow to needs_review, not leave it hanging or crash it -- exercised with a
    non-retryable error so the test doesn't wait through the real 20-minute retry budget."""
    task_queue = f"test-{uuid.uuid4()}"

    @activity.defn(name="extract_document_activity")
    async def fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
        raise ApplicationError("simulated hard failure", non_retryable=True)

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[_fetch(_BOTH_DOCS), fake_extract, _update_status([])],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.reason == "extraction failed or timed out"


async def test_no_face_detected_marks_case_needs_review(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
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
            _update_status([]),
        ],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.face_match_score is None


async def test_sanctions_hit_marks_case_needs_review(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
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
            _update_status([]),
        ],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.sanctions_hit_count == 1


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
            _update_status([]),
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
            _update_status([]),
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
