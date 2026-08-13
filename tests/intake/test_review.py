"""Reviewer auth + review queue endpoint tests against the real dev Postgres (RLS-governed,
same pattern as test_cases.py) and the real dev Temporal server for the decision endpoint's
workflow-signal call.

Cases here are inserted directly at a given status (via conftest._insert_case) rather than run
through the full intake -> Temporal pipeline -- that full path, including a live workflow that
actually receives the decision signal and finalizes, is covered separately by
test_review_e2e.py (Phase 7's stated exit criteria). These tests isolate the review endpoints'
own logic: auth, tenant scoping, and status-transition rules.
"""

from httpx import AsyncClient

from services.intake import db
from tests.intake.conftest import _create_reviewer, _insert_case


async def test_list_review_queue_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/review/cases")
    assert response.status_code == 401


async def test_list_review_queue_rejects_malformed_bearer(client: AsyncClient) -> None:
    response = await client.get("/review/cases", headers={"Authorization": "NotBearer xyz"})
    assert response.status_code == 401


async def test_list_review_queue_rejects_invalid_token(client: AsyncClient) -> None:
    response = await client.get(
        "/review/cases", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


async def test_revoked_reviewer_token_is_rejected(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_id = two_tenants_with_reviewers["a"]["tenant_id"]
    _, revoked_token = await _create_reviewer(tenant_id, "revoked@test.local", revoked=True)

    response = await client.get(
        "/review/cases", headers={"Authorization": f"Bearer {revoked_token}"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Reviewer token revoked"


async def test_list_review_queue_only_returns_needs_review_cases(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    await _insert_case(tenant_a["tenant_id"], status="pending", subject_name="Pending Person")
    needs_review_id = await _insert_case(
        tenant_a["tenant_id"], status="needs_review", subject_name="Ambiguous Person"
    )
    await _insert_case(tenant_a["tenant_id"], status="approved", subject_name="Approved Person")

    response = await client.get(
        "/review/cases", headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"}
    )

    assert response.status_code == 200
    case_ids = [c["case_id"] for c in response.json()]
    assert case_ids == [needs_review_id]


async def test_review_queue_is_tenant_scoped(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    tenant_b = two_tenants_with_reviewers["b"]
    await _insert_case(tenant_a["tenant_id"], status="needs_review")

    response = await client.get(
        "/review/cases", headers={"Authorization": f"Bearer {tenant_b['reviewer_token']}"}
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_claim_updates_case_and_is_advisory_not_a_lock(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="needs_review")

    response = await client.post(
        f"/review/cases/{case_id}/claim",
        headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["claimed_by_reviewer_id"] == tenant_a["reviewer_id"]
    assert body["claimed_at"] is not None

    # A second reviewer can still re-claim -- claiming is a UI signal, not an exclusive lock
    # (see PLAN.md): the decision endpoint below never checks who holds the claim.
    _, other_token = await _create_reviewer(tenant_a["tenant_id"], "other@test.local")
    second_claim = await client.post(
        f"/review/cases/{case_id}/claim", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert second_claim.status_code == 200
    assert second_claim.json()["claimed_by_reviewer_id"] != tenant_a["reviewer_id"]


async def test_claim_cross_tenant_case_is_404(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    tenant_b = two_tenants_with_reviewers["b"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="needs_review")

    response = await client.post(
        f"/review/cases/{case_id}/claim",
        headers={"Authorization": f"Bearer {tenant_b['reviewer_token']}"},
    )

    assert response.status_code == 404


async def test_claim_case_not_awaiting_review_is_404(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="approved")

    response = await client.post(
        f"/review/cases/{case_id}/claim",
        headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
    )

    assert response.status_code == 404


async def test_decision_on_unknown_case_is_404(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    response = await client.post(
        "/review/cases/00000000-0000-0000-0000-000000000000/decision",
        headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
        json={"decision": "approved", "justification": "n/a"},
    )
    assert response.status_code == 404


async def test_decision_cross_tenant_case_is_404(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    tenant_b = two_tenants_with_reviewers["b"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="needs_review")

    response = await client.post(
        f"/review/cases/{case_id}/decision",
        headers={"Authorization": f"Bearer {tenant_b['reviewer_token']}"},
        json={"decision": "approved", "justification": "n/a"},
    )
    assert response.status_code == 404


async def test_decision_on_case_not_awaiting_review_is_409(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="approved")

    response = await client.post(
        f"/review/cases/{case_id}/decision",
        headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
        json={"decision": "rejected", "justification": "n/a"},
    )
    assert response.status_code == 409


async def test_decision_rejects_invalid_decision_value(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants_with_reviewers["a"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="needs_review")

    response = await client.post(
        f"/review/cases/{case_id}/decision",
        headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
        json={"decision": "maybe", "justification": "n/a"},
    )
    assert response.status_code == 422


async def test_decision_records_review_decision_even_when_no_workflow_is_running(
    client: AsyncClient, two_tenants_with_reviewers: dict[str, dict[str, str]]
) -> None:
    """These tests insert cases directly rather than starting them through the real Temporal
    pipeline (that full path is test_review_e2e.py's job), so there is no live workflow for
    this case_id -- the signal RPC comes back NOT_FOUND, which the endpoint surfaces as 409.
    The decision itself must still be durably recorded: that write happens (and commits) before
    the signal attempt, precisely so a reviewer's decision is never silently lost even if the
    workflow side of the handoff fails."""
    tenant_a = two_tenants_with_reviewers["a"]
    case_id = await _insert_case(tenant_a["tenant_id"], status="needs_review")

    response = await client.post(
        f"/review/cases/{case_id}/decision",
        headers={"Authorization": f"Bearer {tenant_a['reviewer_token']}"},
        json={"decision": "approved", "justification": "looks legitimate"},
    )

    assert response.status_code == 409

    pool = db.get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_a["tenant_id"])
        row = await conn.fetchrow(
            "SELECT decision, justification FROM review_decisions WHERE case_id = $1", case_id
        )
    assert row is not None
    assert row["decision"] == "approved"
    assert row["justification"] == "looks legitimate"
