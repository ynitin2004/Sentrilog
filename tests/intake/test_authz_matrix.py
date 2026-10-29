"""The multi-tenant authorization matrix (Phase 12, PLAN.md §9).

Cross-tenant checks already existed per-endpoint, added by whichever phase introduced each
endpoint. The gap this file closes is that they were *scattered*: there was no single place
that enumerated the attack surface, so "is every endpoint covered?" could only be answered by
reading six test files and trusting that the next endpoint added would remember to bring its own
isolation test. Here every tenant-scoped route is one row in a table, which makes an uncovered
endpoint a visible omission rather than an invisible one.

Three distinct properties are exercised, because they fail differently:

1. Resource-ID routes: tenant B naming tenant A's resource gets 404 (not 403 -- the existence
   of another tenant's case is itself privileged, so the response must not distinguish
   "exists but forbidden" from "doesn't exist").
2. Collection routes: tenant B's listing never contains tenant A's rows.
3. Credential-type confusion: a reviewer token can't be used where an API key is required and
   vice versa, since both are opaque bearer tokens hitting the same Authorization header.
"""

import secrets

import pytest
from httpx import AsyncClient

from tests.intake.conftest import _create_reviewer, _insert_case

VALID_CASE_PAYLOAD = {
    "subject_name": "Matrix Subject",
    "subject_dob": "1990-01-01",
    "id_document": {"content_type": "image/jpeg", "size_bytes": 50_000},
    "selfie": {"content_type": "image/jpeg", "size_bytes": 30_000},
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- 1. Resource-ID routes: cross-tenant access must 404 -------------------------------------


async def test_api_key_routes_404_on_another_tenants_resource(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    """Every API-key-authenticated route that takes a resource ID, in one place.

    Each resource is created for real by tenant A through the real endpoint, then tenant B
    attempts to reach it with its own valid key.
    """
    a, b = two_tenants["a"], two_tenants["b"]

    case_id = await _insert_case(a["tenant_id"], status="needs_review")
    key_id = (
        await client.post("/api-keys", json={"name": "a-key"}, headers=_auth(a["api_key"]))
    ).json()["id"]
    reviewer_id = (
        await client.post(
            "/reviewers",
            json={"email": f"matrix-{secrets.token_hex(4)}@test.local", "role": "reviewer"},
            headers=_auth(a["api_key"]),
        )
    ).json()["id"]
    webhook_id = (
        await client.post(
            "/webhooks", json={"url": "https://example.com/hook"}, headers=_auth(a["api_key"])
        )
    ).json()["id"]

    attempts = [
        ("GET", f"/cases/{case_id}", None),
        ("POST", f"/api-keys/{key_id}/revoke", None),
        ("POST", f"/reviewers/{reviewer_id}/revoke", None),
        ("POST", f"/webhooks/{webhook_id}/disable", None),
        ("GET", f"/webhooks/{webhook_id}/deliveries", None),
    ]

    failures = []
    for method, path, body in attempts:
        response = await client.request(method, path, json=body, headers=_auth(b["api_key"]))
        if response.status_code != 404:
            failures.append(f"{method} {path} -> {response.status_code} (expected 404)")

    assert not failures, "cross-tenant access was not denied with 404: " + "; ".join(failures)


async def test_reviewer_routes_404_on_another_tenants_case(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    a, b = two_tenants_with_reviewers["a"], two_tenants_with_reviewers["b"]
    case_id = await _insert_case(a["tenant_id"], status="needs_review")

    attempts = [
        ("GET", f"/review/cases/{case_id}", None),
        ("POST", f"/review/cases/{case_id}/claim", None),
        (
            "POST",
            f"/review/cases/{case_id}/decision",
            {"decision": "approved", "justification": "cross-tenant attempt"},
        ),
    ]

    failures = []
    for method, path, body in attempts:
        response = await client.request(method, path, json=body, headers=_auth(b["reviewer_token"]))
        if response.status_code != 404:
            failures.append(f"{method} {path} -> {response.status_code} (expected 404)")

    assert not failures, "cross-tenant access was not denied with 404: " + "; ".join(failures)


# --- 2. Collection routes: a listing never contains another tenant's rows ---------------------


async def test_collection_routes_never_include_another_tenants_rows(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    a, b = two_tenants_with_reviewers["a"], two_tenants_with_reviewers["b"]

    a_case = await _insert_case(a["tenant_id"], status="needs_review", subject_name="A Subject")
    await client.post("/api-keys", json={"name": "a-only-key"}, headers=_auth(a["api_key"]))
    await client.post(
        "/webhooks", json={"url": "https://a-only.example.com/hook"}, headers=_auth(a["api_key"])
    )

    # Each listing's own identity field -- /cases keys its rows `case_id` while the admin
    # collections use `id`, so the key is part of the table rather than assumed uniform.
    key_listings = [
        ("/cases", "case_id"),
        ("/api-keys", "id"),
        ("/reviewers", "id"),
        ("/webhooks", "id"),
    ]

    failures = []
    for path, id_field in key_listings:
        a_rows = (await client.get(path, headers=_auth(a["api_key"]))).json()
        b_rows = (await client.get(path, headers=_auth(b["api_key"]))).json()
        # An empty listing on both sides would make the overlap check vacuously pass, so prove
        # tenant A actually has rows here before concluding anything about isolation.
        if not a_rows:
            failures.append(f"{path} returned nothing for the tenant that owns rows there")
            continue
        overlap = {row[id_field] for row in a_rows} & {row[id_field] for row in b_rows}
        if overlap:
            failures.append(f"{path} leaked {len(overlap)} row(s) across tenants")

    queue_b = (await client.get("/review/cases", headers=_auth(b["reviewer_token"]))).json()
    if any(row["case_id"] == a_case for row in queue_b):
        failures.append("/review/cases leaked tenant A's case into tenant B's queue")

    assert not failures, "; ".join(failures)


async def test_dashboard_summary_counts_only_the_callers_own_cases(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    # Aggregates leak differently from listings: no row is exposed, but a count that includes
    # another tenant's cases still discloses their volume.
    a, b = two_tenants["a"], two_tenants["b"]
    before = (await client.get("/dashboard/summary", headers=_auth(b["api_key"]))).json()

    for _ in range(3):
        await _insert_case(a["tenant_id"], status="needs_review")

    after = (await client.get("/dashboard/summary", headers=_auth(b["api_key"]))).json()
    assert after["status_counts"] == before["status_counts"]


# --- 3. Credential-type confusion -------------------------------------------------------------


async def test_a_reviewer_token_is_rejected_on_api_key_routes(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    # Both credentials are opaque bearer tokens arriving in the same header, so "valid token,
    # wrong kind" is a real confusion risk rather than a theoretical one.
    token = two_tenants_with_reviewers["a"]["reviewer_token"]
    for path in ("/cases", "/api-keys", "/reviewers", "/webhooks", "/dashboard/summary"):
        response = await client.get(path, headers=_auth(token))
        assert response.status_code == 401, f"{path} accepted a reviewer token"


async def test_an_api_key_is_rejected_on_reviewer_routes(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    api_key = two_tenants_with_reviewers["a"]["api_key"]
    response = await client.get("/review/cases", headers=_auth(api_key))
    assert response.status_code == 401


async def test_revoked_credentials_are_rejected_everywhere(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    a = two_tenants_with_reviewers["a"]
    _, revoked_token = await _create_reviewer(
        a["tenant_id"], f"revoked-{secrets.token_hex(4)}@test.local", revoked=True
    )

    created = await client.post(
        "/api-keys", json={"name": "to-revoke"}, headers=_auth(a["api_key"])
    )
    raw_revoked_key = created.json()["raw_key"]
    await client.post(f"/api-keys/{created.json()['id']}/revoke", headers=_auth(a["api_key"]))

    assert (await client.get("/review/cases", headers=_auth(revoked_token))).status_code == 401
    assert (await client.get("/cases", headers=_auth(raw_revoked_key))).status_code == 401


# --- 4. Role-based authorization within a tenant ----------------------------------------------


@pytest.mark.parametrize("role", ["reviewer", "admin"])
async def test_decision_making_roles_may_claim(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]], role: str
) -> None:
    a = two_tenants_with_reviewers["a"]
    _, token = await _create_reviewer(
        a["tenant_id"], f"{role}-{secrets.token_hex(4)}@test.local", role=role
    )
    case_id = await _insert_case(a["tenant_id"], status="needs_review")

    response = await client.post(f"/review/cases/{case_id}/claim", headers=_auth(token))
    assert response.status_code == 200


async def test_an_auditor_may_read_but_not_claim_or_decide(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    """The gap Phase 12 found: `role` was resolved from the database and carried on every
    request, but never checked -- so an auditor token could approve or reject cases exactly
    like a reviewer's. An auditor's job is to inspect the trail, not to change its outcome.

    403, not 404, deliberately: the caller is inside their own tenant and can already see this
    case in the queue, so there is nothing to conceal, unlike the cross-tenant cases above.
    """
    a = two_tenants_with_reviewers["a"]
    _, auditor_token = await _create_reviewer(
        a["tenant_id"], f"auditor-{secrets.token_hex(4)}@test.local", role="auditor"
    )
    case_id = await _insert_case(a["tenant_id"], status="needs_review")

    # Reads are allowed.
    assert (await client.get("/review/cases", headers=_auth(auditor_token))).status_code == 200
    assert (
        await client.get(f"/review/cases/{case_id}", headers=_auth(auditor_token))
    ).status_code == 200

    # Writes that change a case's outcome are not.
    claim = await client.post(f"/review/cases/{case_id}/claim", headers=_auth(auditor_token))
    assert claim.status_code == 403
    assert "auditor" in claim.json()["detail"]

    decision = await client.post(
        f"/review/cases/{case_id}/decision",
        json={"decision": "approved", "justification": "auditor should not be able to do this"},
        headers=_auth(auditor_token),
    )
    assert decision.status_code == 403


async def test_an_auditors_blocked_decision_leaves_no_trace(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    # A 403 that still wrote the row would be worse than no check at all: the case would carry
    # a decision nobody was authorized to make.
    from services.intake import db

    a = two_tenants_with_reviewers["a"]
    _, auditor_token = await _create_reviewer(
        a["tenant_id"], f"auditor2-{secrets.token_hex(4)}@test.local", role="auditor"
    )
    case_id = await _insert_case(a["tenant_id"], status="needs_review")

    await client.post(
        f"/review/cases/{case_id}/decision",
        json={"decision": "approved", "justification": "should not persist"},
        headers=_auth(auditor_token),
    )

    pool = db.get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a["tenant_id"])
        decisions = await conn.fetchval(
            "SELECT count(*) FROM review_decisions WHERE case_id = $1", case_id
        )
        audit_rows = await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE case_id = $1", case_id
        )
    assert decisions == 0
    assert audit_rows == 0
