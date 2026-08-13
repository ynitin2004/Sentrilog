"""deliver_webhooks_activity tests against the real dev Postgres (sentrilog_app role, same RLS
path as production) and a real local HTTP server (stdlib http.server on an ephemeral port) --
not a mocked httpx client. This proves the actual HMAC-signed POST request goes out over a real
socket and that webhook_deliveries rows are recorded correctly, matching the "real infra over
mocks" standard used throughout this project.
"""

import hashlib
import hmac
import json
import secrets
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import asyncpg
import pytest
import pytest_asyncio

from services.pipeline import db
from services.pipeline.workflows.activities import DeliverWebhooksInput, deliver_webhooks_activity

_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _db_pool() -> AsyncIterator[None]:
    await db.init_pool()
    yield
    await db.close_pool()


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

    def log_message(self, format: str, *args: object) -> None:  # silence stderr noise
        pass


class _FailingHandler(BaseHTTPRequestHandler):
    attempts = 0

    def do_POST(self) -> None:
        self.__class__.attempts += 1
        self.send_response(500)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def _start_server(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[HTTPServer, int]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


@pytest.fixture
def capturing_server() -> Iterator[tuple[HTTPServer, int]]:
    _CapturingHandler.received = []
    server, port = _start_server(_CapturingHandler)
    try:
        yield server, port
    finally:
        server.shutdown()


@pytest.fixture
def failing_server() -> Iterator[tuple[HTTPServer, int]]:
    _FailingHandler.attempts = 0
    server, port = _start_server(_FailingHandler)
    try:
        yield server, port
    finally:
        server.shutdown()


async def _create_tenant_with_webhook(url: str, secret: str) -> tuple[str, str]:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            suffix = secrets.token_hex(4)
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"Webhook Test {suffix}",
                f"webhook-test-{suffix}",
            )
            case_id = await conn.fetchval(
                "INSERT INTO cases (tenant_id, subject_name, status) VALUES ($1, $2, $3) "
                "RETURNING id",
                tenant_id,
                "Jane Doe",
                "needs_review",
            )
            await conn.execute(
                "INSERT INTO webhooks (tenant_id, url, secret) VALUES ($1, $2, $3)",
                tenant_id,
                url,
                secret,
            )
        return str(tenant_id), str(case_id)
    finally:
        await conn.close()


async def _delete_tenant(tenant_id: str) -> None:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM webhook_deliveries WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM cases WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM webhooks WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
    finally:
        await conn.close()


async def test_delivers_to_real_http_server_with_valid_hmac_signature(
    capturing_server: tuple[HTTPServer, int],
) -> None:
    _, port = capturing_server
    secret = "test-webhook-secret"
    tenant_id, case_id = await _create_tenant_with_webhook(f"http://127.0.0.1:{port}/", secret)

    try:
        result = await deliver_webhooks_activity(
            DeliverWebhooksInput(
                tenant_id=tenant_id,
                case_id=case_id,
                event_type="case.decided",
                decision="approved",
                risk_score=0.05,
            )
        )

        assert result.delivered_count == 1
        assert result.failed_count == 0
        assert len(_CapturingHandler.received) == 1

        received = _CapturingHandler.received[0]
        body = received["body"]
        assert isinstance(body, bytes)
        expected_signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        assert received["signature"] == expected_signature

        payload = json.loads(body)
        assert payload["event"] == "case.decided"
        assert payload["case_id"] == case_id
        assert payload["decision"] == "approved"
        assert payload["risk_score"] == 0.05

        async with db.tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT status, attempt_count FROM webhook_deliveries WHERE case_id = $1",
                case_id,
            )
        assert row is not None
        assert row["status"] == "delivered"
        assert row["attempt_count"] == 1
    finally:
        await _delete_tenant(tenant_id)


async def test_no_registered_webhooks_is_a_no_op(capturing_server: tuple[HTTPServer, int]) -> None:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    suffix = secrets.token_hex(4)
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
        f"No Webhook Test {suffix}",
        f"no-webhook-test-{suffix}",
    )
    case_id = await conn.fetchval(
        "INSERT INTO cases (tenant_id, subject_name, status) VALUES ($1, $2, $3) RETURNING id",
        str(tenant_id),
        "Jane Doe",
        "needs_review",
    )
    await conn.close()

    try:
        result = await deliver_webhooks_activity(
            DeliverWebhooksInput(
                tenant_id=str(tenant_id),
                case_id=str(case_id),
                event_type="case.decided",
                decision="approved",
                risk_score=None,
            )
        )
        assert result.delivered_count == 0
        assert result.failed_count == 0
        assert _CapturingHandler.received == []
    finally:
        await _delete_tenant(str(tenant_id))


async def test_disabled_webhook_is_skipped(capturing_server: tuple[HTTPServer, int]) -> None:
    _, port = capturing_server
    tenant_id, case_id = await _create_tenant_with_webhook(
        f"http://127.0.0.1:{port}/", "unused-secret"
    )
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        await conn.execute(
            "UPDATE webhooks SET disabled_at = now() WHERE tenant_id = $1", tenant_id
        )
    finally:
        await conn.close()

    try:
        result = await deliver_webhooks_activity(
            DeliverWebhooksInput(
                tenant_id=tenant_id,
                case_id=case_id,
                event_type="case.decided",
                decision="rejected",
                risk_score=None,
            )
        )
        assert result.delivered_count == 0
        assert result.failed_count == 0
        assert _CapturingHandler.received == []
    finally:
        await _delete_tenant(tenant_id)


async def test_unreachable_webhook_is_retried_then_recorded_as_failed(
    failing_server: tuple[HTTPServer, int],
) -> None:
    _, port = failing_server
    tenant_id, case_id = await _create_tenant_with_webhook(
        f"http://127.0.0.1:{port}/", "unused-secret"
    )

    try:
        result = await deliver_webhooks_activity(
            DeliverWebhooksInput(
                tenant_id=tenant_id,
                case_id=case_id,
                event_type="case.decided",
                decision="rejected",
                risk_score=None,
            )
        )

        assert result.delivered_count == 0
        assert result.failed_count == 1
        # 3 attempts: _WEBHOOK_MAX_ATTEMPTS in activities.py
        assert _FailingHandler.attempts == 3

        async with db.tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT status, attempt_count FROM webhook_deliveries WHERE case_id = $1",
                case_id,
            )
        assert row is not None
        assert row["status"] == "failed"
        assert row["attempt_count"] == 3
    finally:
        await _delete_tenant(tenant_id)


async def test_two_active_webhooks_both_receive_delivery(
    capturing_server: tuple[HTTPServer, int],
) -> None:
    _, port = capturing_server
    tenant_id, case_id = await _create_tenant_with_webhook(
        f"http://127.0.0.1:{port}/first", "secret-one"
    )
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        await conn.execute(
            "INSERT INTO webhooks (tenant_id, url, secret) VALUES ($1, $2, $3)",
            tenant_id,
            f"http://127.0.0.1:{port}/second",
            "secret-two",
        )
    finally:
        await conn.close()

    try:
        result = await deliver_webhooks_activity(
            DeliverWebhooksInput(
                tenant_id=tenant_id,
                case_id=case_id,
                event_type="case.decided",
                decision="approved",
                risk_score=0.1,
            )
        )
        assert result.delivered_count == 2
        assert len(_CapturingHandler.received) == 2

        async with db.tenant_connection(tenant_id) as conn2:
            rows = await conn2.fetch(
                "SELECT status FROM webhook_deliveries WHERE case_id = $1", case_id
            )
        assert len(rows) == 2
        assert all(r["status"] == "delivered" for r in rows)
    finally:
        await _delete_tenant(tenant_id)
