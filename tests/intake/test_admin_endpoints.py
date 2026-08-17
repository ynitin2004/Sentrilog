"""Phase 9's new admin/tenant-management endpoints (API keys, webhooks, reviewers, cases list,
dashboard summary), tested against real RLS-governed Postgres -- same pattern as test_cases.py
and test_review.py. These are exactly the surface where a cross-tenant leak would be worst
(they manage credentials and reveal tenant-wide data), so every endpoint gets the same explicit
isolation test the review endpoints already have, not a shortcut.
"""

import asyncpg
from httpx import AsyncClient

from tests.intake.conftest import _insert_case

_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"


# --- API keys -------------------------------------------------------------------------------


async def test_create_and_list_api_key(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]

    create_response = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"name": "production-intake"},
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["name"] == "production-intake"
    assert body["revoked_at"] is None
    raw_key = body["raw_key"]
    assert raw_key  # a real usable key, not just an id

    # The new key actually works for the real, existing /cases endpoint.
    whoami_response = await client.get(
        "/cases/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert whoami_response.status_code == 404  # authenticated, just no such case

    list_response = await client.get(
        "/api-keys", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    assert list_response.status_code == 200
    names = [k["name"] for k in list_response.json()]
    assert "production-intake" in names
    assert all("raw_key" not in k for k in list_response.json())  # never returned again


async def test_api_keys_are_tenant_scoped(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a, tenant_b = two_tenants["a"], two_tenants["b"]
    await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"name": "tenant-a-only"},
    )

    list_response = await client.get(
        "/api-keys", headers={"Authorization": f"Bearer {tenant_b['api_key']}"}
    )
    names = [k["name"] for k in list_response.json()]
    assert "tenant-a-only" not in names


async def test_revoke_api_key_is_idempotent_and_404s_for_unknown_or_cross_tenant(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a, tenant_b = two_tenants["a"], two_tenants["b"]
    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"name": "to-revoke"},
    )
    key_id = created.json()["id"]

    first = await client.post(
        f"/api-keys/{key_id}/revoke", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    assert first.status_code == 200
    revoked_at = first.json()["revoked_at"]
    assert revoked_at is not None

    second = await client.post(
        f"/api-keys/{key_id}/revoke", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    assert second.status_code == 200
    assert second.json()["revoked_at"] == revoked_at  # unchanged, not re-stamped

    cross_tenant = await client.post(
        f"/api-keys/{key_id}/revoke", headers={"Authorization": f"Bearer {tenant_b['api_key']}"}
    )
    assert cross_tenant.status_code == 404

    unknown = await client.post(
        "/api-keys/00000000-0000-0000-0000-000000000000/revoke",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
    )
    assert unknown.status_code == 404


# --- Reviewers --------------------------------------------------------------------------------


async def test_create_and_list_reviewer(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]

    create_response = await client.post(
        "/reviewers",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"email": "new-reviewer@test.local", "role": "reviewer"},
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["email"] == "new-reviewer@test.local"
    raw_token = body["raw_token"]
    assert raw_token

    # The new token actually works against a real reviewer-auth endpoint.
    queue_response = await client.get(
        "/review/cases", headers={"Authorization": f"Bearer {raw_token}"}
    )
    assert queue_response.status_code == 200

    list_response = await client.get(
        "/reviewers", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    emails = [r["email"] for r in list_response.json()]
    assert "new-reviewer@test.local" in emails
    assert all("raw_token" not in r for r in list_response.json())


async def test_duplicate_reviewer_email_is_409_not_500(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    payload = {"email": "dup@test.local", "role": "reviewer"}

    first = await client.post(
        "/reviewers", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}, json=payload
    )
    assert first.status_code == 201

    second = await client.post(
        "/reviewers", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}, json=payload
    )
    assert second.status_code == 409


async def test_reviewers_are_tenant_scoped(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a, tenant_b = two_tenants["a"], two_tenants["b"]
    await client.post(
        "/reviewers",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"email": "tenant-a-reviewer@test.local"},
    )

    list_response = await client.get(
        "/reviewers", headers={"Authorization": f"Bearer {tenant_b['api_key']}"}
    )
    emails = [r["email"] for r in list_response.json()]
    assert "tenant-a-reviewer@test.local" not in emails


async def test_revoke_reviewer_cross_tenant_is_404(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a, tenant_b = two_tenants["a"], two_tenants["b"]
    created = await client.post(
        "/reviewers",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"email": "revoke-me@test.local"},
    )
    reviewer_id = created.json()["id"]

    response = await client.post(
        f"/reviewers/{reviewer_id}/revoke",
        headers={"Authorization": f"Bearer {tenant_b['api_key']}"},
    )
    assert response.status_code == 404


# --- Webhooks ---------------------------------------------------------------------------------


async def test_create_webhook_rejects_non_https(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    response = await client.post(
        "/webhooks",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"url": "http://insecure.example.com/hook"},
    )
    assert response.status_code == 422


async def test_create_and_list_webhook(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]

    create_response = await client.post(
        "/webhooks",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"url": "https://example.com/hook"},
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["url"] == "https://example.com/hook"
    assert body["secret"]  # returned once, at creation

    list_response = await client.get(
        "/webhooks", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    urls = [w["url"] for w in list_response.json()]
    assert "https://example.com/hook" in urls
    assert all("secret" not in w for w in list_response.json())  # never returned again


async def test_webhooks_are_tenant_scoped_including_disable_and_deliveries(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a, tenant_b = two_tenants["a"], two_tenants["b"]
    created = await client.post(
        "/webhooks",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"url": "https://tenant-a.example.com/hook"},
    )
    webhook_id = created.json()["id"]

    list_response = await client.get(
        "/webhooks", headers={"Authorization": f"Bearer {tenant_b['api_key']}"}
    )
    assert list_response.json() == []

    disable_response = await client.post(
        f"/webhooks/{webhook_id}/disable",
        headers={"Authorization": f"Bearer {tenant_b['api_key']}"},
    )
    assert disable_response.status_code == 404

    deliveries_response = await client.get(
        f"/webhooks/{webhook_id}/deliveries",
        headers={"Authorization": f"Bearer {tenant_b['api_key']}"},
    )
    assert deliveries_response.status_code == 404


async def test_disable_webhook_is_idempotent(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    created = await client.post(
        "/webhooks",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"url": "https://example.com/hook"},
    )
    webhook_id = created.json()["id"]

    first = await client.post(
        f"/webhooks/{webhook_id}/disable",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
    )
    second = await client.post(
        f"/webhooks/{webhook_id}/disable",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["disabled_at"] == second.json()["disabled_at"]


async def test_webhook_deliveries_lists_only_that_webhooks_rows(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    created = await client.post(
        "/webhooks",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
        json={"url": "https://example.com/hook"},
    )
    webhook_id = created.json()["id"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="approved")

    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        await conn.execute(
            "INSERT INTO webhook_deliveries (tenant_id, webhook_id, case_id, event_type, "
            "payload, status, attempt_count, last_attempted_at) "
            "VALUES ($1, $2, $3, 'case.decided', '{}'::jsonb, 'delivered', 1, now())",
            tenant_a["tenant_id"],
            webhook_id,
            case_id,
        )
    finally:
        await conn.close()

    response = await client.get(
        f"/webhooks/{webhook_id}/deliveries",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
    )
    assert response.status_code == 200
    deliveries = response.json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "delivered"
    assert deliveries[0]["case_id"] == case_id


# --- Cases list -------------------------------------------------------------------------------


async def test_list_cases_returns_all_statuses_tenant_scoped(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a, tenant_b = two_tenants["a"], two_tenants["b"]
    await _insert_case(tenant_a["tenant_id"], status="needs_review", subject_name="A1")
    await _insert_case(tenant_a["tenant_id"], status="approved", subject_name="A2")
    await _insert_case(tenant_b["tenant_id"], status="approved", subject_name="B1")

    response = await client.get(
        "/cases", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    assert response.status_code == 200
    names = {c["subject_name"] for c in response.json()}
    assert names == {"A1", "A2"}


async def test_list_cases_filters_by_status(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    await _insert_case(tenant_a["tenant_id"], status="needs_review", subject_name="Ambiguous")
    await _insert_case(tenant_a["tenant_id"], status="approved", subject_name="Clean")

    response = await client.get(
        "/cases?status=needs_review", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    assert response.status_code == 200
    names = [c["subject_name"] for c in response.json()]
    assert names == ["Ambiguous"]


async def test_list_cases_rejects_invalid_status(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    response = await client.get(
        "/cases?status=not-a-real-status",
        headers={"Authorization": f"Bearer {tenant_a['api_key']}"},
    )
    assert response.status_code == 422


# --- Dashboard summary ------------------------------------------------------------------------


async def test_dashboard_summary_zero_fills_every_status(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    response = await client.get(
        "/dashboard/summary", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    assert response.status_code == 200
    counts = response.json()["status_counts"]
    assert counts == {
        "pending": 0,
        "processing": 0,
        "needs_review": 0,
        "approved": 0,
        "rejected": 0,
    }


async def test_dashboard_summary_counts_and_scoping(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a, tenant_b = two_tenants["a"], two_tenants["b"]
    await _insert_case(tenant_a["tenant_id"], status="needs_review")
    await _insert_case(tenant_a["tenant_id"], status="needs_review")
    await _insert_case(tenant_a["tenant_id"], status="approved")
    await _insert_case(tenant_b["tenant_id"], status="approved")  # must not leak into A's counts

    response = await client.get(
        "/dashboard/summary", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    counts = response.json()["status_counts"]
    assert counts["needs_review"] == 2
    assert counts["approved"] == 1


async def test_dashboard_summary_recent_activity_reflects_decisions(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    case_id = await _insert_case(
        tenant_a["tenant_id"], status="needs_review", subject_name="Decided Person"
    )

    decision_response = await client.post(
        f"/review/cases/{case_id}/decision",
        headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
        json={"decision": "approved", "justification": "looks fine"},
    )
    # No live workflow for a directly-inserted case -- 409 is expected (see test_review.py's
    # equivalent case), but the decision itself is still recorded, which is what this test cares
    # about.
    assert decision_response.status_code == 409

    response = await client.get(
        "/dashboard/summary", headers={"Authorization": f"Bearer {tenant_a['api_key']}"}
    )
    events = [a["event"] for a in response.json()["recent_activity"]]
    assert any("approved by" in e for e in events)
