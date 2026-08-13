"""Phase 7's stated exit criteria, exercised as one real end-to-end flow: a case created
through the real intake API parks in the review queue, a reviewer decides it through the real
review endpoints, the running Temporal workflow picks up the signal and completes, and a real
webhook delivery is recorded -- while a reviewer from a different tenant can't see or act on it.

fetch/extract are faked on the test Worker (returning a deliberately ambiguous extraction, the
same "low confidence -> needs_review" path exercised in isolation by
tests/pipeline/workflows/test_kyc_case_workflow.py) so this test doesn't depend on real
S3 uploads, EasyOCR, or Gemini -- those integrations are already proven for real in Phase 4/5's
own tests. What this test proves instead, for real, is everything Phase 7 adds on top: the
signal-based human-in-the-loop handoff, finalize_case_activity's DB write, and
deliver_webhooks_activity's real HTTP delivery -- all real implementations, real Temporal
server, real Postgres, registered on the exact task queue (kyc-case-standard) intake's own
plan_tier routing sends a 'standard' tenant's workflow to.

Known limitation, not silently papered over: this assumes no other worker is already polling
kyc-case-standard locally (docker-compose doesn't run services/pipeline/workflows/worker.py --
it's started manually) -- if one were, both workers would race for the same activity tasks and
this test could flake on which one wins.
"""

import asyncio
import hashlib
import hmac
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import asyncpg
import pytest_asyncio
from httpx import AsyncClient
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from services.pipeline import db as pipeline_db
from services.pipeline.config import settings as pipeline_settings
from services.pipeline.workflows.activities import (
    CaseDocuments,
    DocumentRef,
    ExtractDocumentInput,
    ExtractDocumentOutput,
    FetchDocumentInput,
    deliver_webhooks_activity,
    finalize_case_activity,
    update_case_status_activity,
)
from services.pipeline.workflows.kyc_case import KycCaseWorkflow

_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"
_TASK_QUEUE = "kyc-case-standard"  # matches task_queue_for_plan_tier("standard")


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _pipeline_db_pool() -> AsyncIterator[None]:
    await pipeline_db.init_pool()
    yield
    await pipeline_db.close_pool()


class _CapturingHandler(BaseHTTPRequestHandler):
    received: list[dict[str, object]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.__class__.received.append(
            {"body": body, "signature": self.headers.get("X-Sentrilog-Signature")}
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest_asyncio.fixture
async def webhook_server() -> AsyncIterator[tuple[HTTPServer, int]]:
    _CapturingHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, port
    finally:
        server.shutdown()


@pytest_asyncio.fixture
async def registered_webhook(
    two_tenants_with_reviewers: dict[str, dict[str, str]],
    webhook_server: tuple[HTTPServer, int],
) -> AsyncIterator[str]:
    _, port = webhook_server
    tenant_id = two_tenants_with_reviewers["a"]["tenant_id"]
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        await conn.execute(
            "INSERT INTO webhooks (tenant_id, url, secret) VALUES ($1, $2, $3)",
            tenant_id,
            f"http://127.0.0.1:{port}/",
            "e2e-test-secret",
        )
    finally:
        await conn.close()
    yield "e2e-test-secret"


def _ambiguous_fetch() -> Any:
    @activity.defn(name="fetch_case_documents_activity")
    async def fake_fetch(input: FetchDocumentInput) -> CaseDocuments:
        return CaseDocuments(
            id_document=DocumentRef(document_id="doc-id", s3_key="fake/id_document"),
            selfie=DocumentRef(document_id="doc-selfie", s3_key="fake/selfie"),
        )

    return fake_fetch


def _ambiguous_extract() -> Any:
    @activity.defn(name="extract_document_activity")
    async def fake_extract(input: ExtractDocumentInput) -> ExtractDocumentOutput:
        return ExtractDocumentOutput(
            needs_review=True,
            confidence=0.4,
            method="vlm",
            reason="document image quality too low to extract reliably",
            full_name=None,
        )

    return fake_extract


async def _wait_until(
    condition: Callable[[], Awaitable[bool]], timeout_seconds: float = 15.0
) -> None:
    elapsed = 0.0
    step = 0.1
    while elapsed < timeout_seconds:
        if await condition():
            return
        await asyncio.sleep(step)
        elapsed += step
    raise AssertionError(f"condition not met within {timeout_seconds}s")


async def test_ambiguous_case_parks_reviewer_decides_workflow_completes_webhook_delivered(
    client: AsyncClient,
    two_tenants_with_reviewers: dict[str, dict[str, str]],
    registered_webhook: str,
    webhook_server: tuple[HTTPServer, int],
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    tenant_b = two_tenants_with_reviewers["b"]
    secret = registered_webhook

    create_response = await client.post(
        "/cases",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={
            "subject_name": "Ambiguous Subject",
            "id_document": {"content_type": "image/jpeg", "size_bytes": 1000},
            "selfie": {"content_type": "image/jpeg", "size_bytes": 1000},
        },
    )
    assert create_response.status_code == 201
    case_id = create_response.json()["case_id"]

    temporal_client = await Client.connect(
        pipeline_settings.temporal_address, namespace=pipeline_settings.temporal_namespace
    )

    async with Worker(
        temporal_client,
        task_queue=_TASK_QUEUE,
        workflows=[KycCaseWorkflow],
        activities=[
            _ambiguous_fetch(),
            _ambiguous_extract(),
            update_case_status_activity,
            finalize_case_activity,
            deliver_webhooks_activity,
        ],
    ):
        # 1. The case parks: reaches needs_review and is visible in tenant A's queue.
        async def _is_parked() -> bool:
            resp = await client.get(
                "/review/cases", headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"}
            )
            return any(c["case_id"] == case_id for c in resp.json())

        await _wait_until(_is_parked)

        # 2. A reviewer from a different tenant cannot see or act on it.
        cross_tenant_list = await client.get(
            "/review/cases", headers={"Authorization": f"Bearer {tenant_b['reviewer_token']}"}
        )
        assert all(c["case_id"] != case_id for c in cross_tenant_list.json())

        cross_tenant_decision = await client.post(
            f"/review/cases/{case_id}/decision",
            headers={"Authorization": f"Bearer {tenant_b['reviewer_token']}"},
            json={"decision": "approved", "justification": "not my case"},
        )
        assert cross_tenant_decision.status_code == 404

        # 3. The rightful reviewer claims and decides.
        claim_response = await client.post(
            f"/review/cases/{case_id}/claim",
            headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
        )
        assert claim_response.status_code == 200

        decision_response = await client.post(
            f"/review/cases/{case_id}/decision",
            headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
            json={"decision": "approved", "justification": "manually verified, looks legitimate"},
        )
        assert decision_response.status_code == 200
        assert decision_response.json()["decision"] == "approved"

        # 4. The workflow picks up the signal and completes: status flips to approved.
        async def _is_approved() -> bool:
            resp = await client.get(
                f"/cases/{case_id}", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
            )
            body = resp.json()
            return bool(body["status"] == "approved" and body["decision"] == "approved")

        await _wait_until(_is_approved)

        # 5. A real webhook delivery went out and was recorded.
        async def _webhook_delivered() -> bool:
            return len(_CapturingHandler.received) >= 1

        await _wait_until(_webhook_delivered)

    received = _CapturingHandler.received[0]
    body = received["body"]
    assert isinstance(body, bytes)
    expected_signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert received["signature"] == expected_signature

    payload = json.loads(body)
    assert payload["case_id"] == case_id
    assert payload["decision"] == "approved"

    async with pipeline_db.tenant_connection(tenant_a["tenant_id"]) as conn:
        delivery_row = await conn.fetchrow(
            "SELECT status FROM webhook_deliveries WHERE case_id = $1", case_id
        )
    assert delivery_row is not None
    assert delivery_row["status"] == "delivered"
