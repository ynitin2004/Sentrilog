"""Tests for GET /events/stream (Phase 10 real-time SSE) against real Postgres LISTEN/NOTIFY --
no mocked pub/sub.

Auth rejection is tested through the real HTTP layer (client.get(...) returns before any
streaming starts, since Depends(require_any_tenant) raises before the generator is ever
created). A *successful* connection is deliberately NOT driven through httpx's ASGITransport:
that transport awaits the whole ASGI application call -- including fully draining the response
body -- before it will hand a Response back to the caller, so an infinite SSE stream deadlocks
it (confirmed while writing this file: `async with client.stream(...)` never returns). Real
browsers and real servers don't have this limitation; only httpx's in-process test transport
does. So the generator that actually produces SSE frames (_case_events_generator) is tested
directly instead -- same real asyncpg LISTEN/NOTIFY, same tenant-filtering code, just without
routing it through the one test double that can't represent a long-lived stream. The claim
endpoint's real pg_notify() call is verified the same way finalize_case_activity's is in
tests/pipeline/workflows/test_risk_and_finalize_activities.py: a real dedicated LISTEN
connection, not a mock.
"""

import asyncio
import json
from typing import cast

import pytest
from fastapi import Request
from httpx import AsyncClient

from services.intake import db
from services.intake.main import _case_events_generator
from tests.intake.conftest import _insert_case


class _StubRequest:
    """A minimal stand-in for FastAPI's Request -- the generator only ever calls
    is_disconnected(); these tests drive completion via gen.aclose() instead of waiting for a
    disconnect signal, so it never needs to return True. cast() to Request at each call site
    below is the deliberately narrow type lie this stub requires."""

    async def is_disconnected(self) -> bool:
        return False


async def test_stream_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/events/stream")
    assert response.status_code == 401


async def test_stream_rejects_malformed_bearer(client: AsyncClient) -> None:
    response = await client.get("/events/stream", headers={"Authorization": "NotBearer xyz"})
    assert response.status_code == 401


async def test_stream_rejects_invalid_token(client: AsyncClient) -> None:
    response = await client.get(
        "/events/stream", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


async def test_generator_delivers_a_matching_tenant_event(
    two_tenants: dict[str, dict[str, str]],
) -> None:
    tenant_a_id = two_tenants["a"]["tenant_id"]
    gen = _case_events_generator(cast(Request, _StubRequest()), tenant_a_id)
    try:
        first = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
        assert first == "retry: 3000\n\n"  # add_listener() has completed by this point

        pool = db.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT pg_notify('case_events', $1)",
                json.dumps(
                    {
                        "tenant_id": tenant_a_id,
                        "case_id": "case-123",
                        "status": "approved",
                        "decision": "approved",
                    }
                ),
            )

        chunk = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
        assert chunk.startswith("event: case_status_changed\n")
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line.removeprefix("data: "))
        assert payload == {
            "tenant_id": tenant_a_id,
            "case_id": "case-123",
            "status": "approved",
            "decision": "approved",
        }
    finally:
        await gen.aclose()


async def test_generator_never_delivers_another_tenants_event(
    two_tenants: dict[str, dict[str, str]],
) -> None:
    tenant_a_id = two_tenants["a"]["tenant_id"]
    tenant_b_id = two_tenants["b"]["tenant_id"]
    gen = _case_events_generator(cast(Request, _StubRequest()), tenant_a_id)
    try:
        first = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
        assert first == "retry: 3000\n\n"

        pool = db.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT pg_notify('case_events', $1)",
                json.dumps(
                    {
                        "tenant_id": tenant_b_id,
                        "case_id": "case-456",
                        "status": "approved",
                        "decision": "approved",
                    }
                ),
            )

        # tenant B's event is silently dropped by the tenant_id check in the notify callback,
        # not queued at all -- so the next chunk never arrives, and this must time out. A short
        # timeout (well under the 15s keepalive interval) is the correct assertion here, not a
        # flaky race: if isolation broke, this would resolve almost instantly instead.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(gen.__anext__(), timeout=1.5)
    finally:
        await gen.aclose()


async def test_claim_endpoint_fires_a_real_case_event_notification(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="needs_review")

    queue: asyncio.Queue[str] = asyncio.Queue()
    listener_conn = await db.raw_connection()
    await listener_conn.add_listener(
        "case_events", lambda _c, _p, _ch, payload: queue.put_nowait(payload)
    )
    try:
        response = await client.post(
            f"/review/cases/{case_id}/claim",
            headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
        )
        assert response.status_code == 200

        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        event = json.loads(payload)
        assert event["case_id"] == case_id
        assert event["status"] == "needs_review"
        assert event["claimed_by_reviewer_id"] == tenant_a["reviewer_id"]
    finally:
        await listener_conn.close()


async def test_claiming_an_already_claimed_case_does_not_notify(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="approved")

    queue: asyncio.Queue[str] = asyncio.Queue()
    listener_conn = await db.raw_connection()
    await listener_conn.add_listener(
        "case_events", lambda _c, _p, _ch, payload: queue.put_nowait(payload)
    )
    try:
        response = await client.post(
            f"/review/cases/{case_id}/claim",
            headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
        )
        assert response.status_code == 404

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=1.5)
    finally:
        await listener_conn.close()
