"""Workflow control-flow tests against the real dev Temporal server (already running via
docker-compose, same as Phase 3's tests use the real Postgres/MinIO rather than mocks). The
activities are swapped for fakes registered under the real activity names -- Temporal matches
activities by name, not Python object identity, so KycCaseWorkflow's own
workflow.execute_activity(fetch_id_document_activity, ...) calls transparently run whichever
implementation the test's Worker registers. This isolates the workflow's control flow (what
PLAN.md Phase 5 actually asks to be tested) from the real activities' own implementation,
which is covered separately by inspection and the real end-to-end smoke test.

Each test uses its own task queue and workflow ID (uuid4) so concurrent test runs, and the
real dev worker if it happens to be running, never collide.
"""

import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.worker import Worker

from services.pipeline.config import settings
from services.pipeline.workflows.activities import (
    DocumentRef,
    ExtractDocumentInput,
    ExtractDocumentOutput,
    FetchDocumentInput,
    UpdateCaseStatusInput,
)
from services.pipeline.workflows.contracts import KycCaseInput, KycCaseResult
from services.pipeline.workflows.kyc_case import KycCaseWorkflow


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


async def test_no_id_document_marks_case_needs_review(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []

    @activity.defn(name="fetch_id_document_activity")
    async def fake_fetch(input: FetchDocumentInput) -> DocumentRef | None:
        return None

    @activity.defn(name="update_case_status_activity")
    async def fake_update_status(input: UpdateCaseStatusInput) -> None:
        status_updates.append(input.status)

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[fake_fetch, fake_update_status],
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
    """Regression test for a real bug found via the manual worker-restart resilience test:
    fetch_id_document_activity's failure was originally uncaught, so exhausting its retries
    crashed the entire workflow (WorkflowFailureError) instead of resolving to needs_review
    like every other failure mode in this workflow."""
    task_queue = f"test-{uuid.uuid4()}"

    @activity.defn(name="fetch_id_document_activity")
    async def fake_fetch(input: FetchDocumentInput) -> DocumentRef | None:
        raise ApplicationError("simulated hard failure", non_retryable=True)

    @activity.defn(name="update_case_status_activity")
    async def fake_update_status(input: UpdateCaseStatusInput) -> None:
        pass

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[fake_fetch, fake_update_status],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.reason == "failed to fetch case documents"


async def test_successful_extraction_marks_case_processing(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"
    status_updates: list[str] = []

    @activity.defn(name="fetch_id_document_activity")
    async def fake_fetch(input: FetchDocumentInput) -> DocumentRef | None:
        return DocumentRef(document_id="doc-1", s3_key="tenant/case/id_document")

    @activity.defn(name="extract_document_activity")
    async def fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
        return ExtractDocumentOutput(needs_review=False, confidence=0.99, method="mrz", reason=None)

    @activity.defn(name="update_case_status_activity")
    async def fake_update_status(input: UpdateCaseStatusInput) -> None:
        status_updates.append(input.status)

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[fake_fetch, fake_extract, fake_update_status],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "processing"
    assert result.confidence == 0.99
    assert result.method == "mrz"
    assert status_updates == ["processing"]


async def test_low_confidence_extraction_marks_case_needs_review(temporal_client: Client) -> None:
    task_queue = f"test-{uuid.uuid4()}"

    @activity.defn(name="fetch_id_document_activity")
    async def fake_fetch(input: FetchDocumentInput) -> DocumentRef | None:
        return DocumentRef(document_id="doc-1", s3_key="tenant/case/id_document")

    @activity.defn(name="extract_document_activity")
    async def fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
        return ExtractDocumentOutput(
            needs_review=True, confidence=0.0, method="vlm", reason="exhausted retries"
        )

    @activity.defn(name="update_case_status_activity")
    async def fake_update_status(input: UpdateCaseStatusInput) -> None:
        pass

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[fake_fetch, fake_extract, fake_update_status],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.reason == "exhausted retries"


async def test_extraction_activity_hard_failure_marks_case_needs_review(
    temporal_client: Client,
) -> None:
    """The activity itself erroring out (not just returning needs_review=True) must still
    resolve the workflow to needs_review, not leave it hanging or crash it -- exercised with a
    non-retryable error so the test doesn't wait through the real 20-minute retry budget."""
    task_queue = f"test-{uuid.uuid4()}"

    @activity.defn(name="fetch_id_document_activity")
    async def fake_fetch(input: FetchDocumentInput) -> DocumentRef | None:
        return DocumentRef(document_id="doc-1", s3_key="tenant/case/id_document")

    @activity.defn(name="extract_document_activity")
    async def fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
        raise ApplicationError("simulated hard failure", non_retryable=True)

    @activity.defn(name="update_case_status_activity")
    async def fake_update_status(input: UpdateCaseStatusInput) -> None:
        pass

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[fake_fetch, fake_extract, fake_update_status],
    ):
        result = await _run_workflow(
            temporal_client, task_queue, KycCaseInput(tenant_id="t1", case_id="c1")
        )

    assert result.status == "needs_review"
    assert result.reason == "extraction failed or timed out"


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

    @activity.defn(name="fetch_id_document_activity")
    async def fake_fetch(input: FetchDocumentInput) -> DocumentRef | None:
        return None

    @activity.defn(name="update_case_status_activity")
    async def fake_update_status(input: UpdateCaseStatusInput) -> None:
        pass

    async with Worker(
        temporal_client,
        task_queue=task_queue,
        workflows=[KycCaseWorkflow],
        activities=[fake_fetch, fake_update_status],
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
