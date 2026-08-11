"""Starts kyc_case workflows from the intake API when a case is created.

Starts by string workflow-type name rather than importing KycCaseWorkflow directly: the
workflow module imports activities.py, which imports boto3/easyocr/google-genai at module
level (easyocr alone pulls in torch) -- importing that into the intake process for code that
only ever needs to reference the workflow by name would transitively drag all of it along.
Confirmed by observing it happen: intake's startup went from near-instant to ~12s the first
time this imported the workflow class directly, purely from unrelated OCR/ML library loading.
Only services.pipeline.workflows.contracts (plain dataclasses, no heavy imports) and
.task_queues (routing only) are imported here.
"""

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from ..pipeline.workflows.contracts import KYC_CASE_WORKFLOW_NAME, KycCaseInput
from ..pipeline.workflows.task_queues import task_queue_for_plan_tier
from .config import settings

_client: Client | None = None


async def get_temporal_client() -> Client:
    global _client
    if _client is None:
        _client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
    return _client


def workflow_id_for_case(case_id: str) -> str:
    return f"kyc-case-{case_id}"


async def start_kyc_case_workflow(*, tenant_id: str, case_id: str, plan_tier: str) -> None:
    """Idempotent: a deterministic workflow ID means re-starting for the same case_id (e.g. on
    an Idempotency-Key replay, or a client retry after a transient failure here) is a no-op,
    not an error -- WorkflowAlreadyStartedError means the earlier attempt already succeeded.
    """
    client = await get_temporal_client()
    try:
        await client.start_workflow(
            KYC_CASE_WORKFLOW_NAME,
            KycCaseInput(tenant_id=tenant_id, case_id=case_id),
            id=workflow_id_for_case(case_id),
            task_queue=task_queue_for_plan_tier(plan_tier),
        )
    except WorkflowAlreadyStartedError:
        pass
