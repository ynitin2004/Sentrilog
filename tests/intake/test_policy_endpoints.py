"""GET/PUT /policy and GET /policy/versions against the real API and database (Phase 13b):
versions only ever grow, tenants never see each other's policies, and a case is pinned to the
version current when it was created."""

import secrets
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

from services.intake import db

VALID_CASE = {
    "subject_name": "Alice Test",
    "subject_dob": "1990-01-01",
    "id_document": {"content_type": "image/jpeg", "size_bytes": 50_000},
    "selfie": {"content_type": "image/jpeg", "size_bytes": 30_000},
}
STRICT: dict[str, Any] = {
    "auto_clear": {"min_combined": 0.9, "face_match_weight": 0.7, "extraction_weight": 0.3},
    "document_rules": {"review_expired": True, "expiring_within_days": 30},
    "min_face_match": 0.6,
    "review_nationalities": ["prk"],
}


def _auth(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def _create_case(client: AsyncClient, api_key: str) -> str:
    resp = await client.post(
        "/cases",
        json=VALID_CASE,
        headers={**_auth(api_key), "Idempotency-Key": secrets.token_hex(8)},
    )
    assert resp.status_code == 201
    case_id: str = resp.json()["case_id"]
    return case_id


async def _policy_version_of(tenant_id: str, case_id: str) -> int:
    async with db.tenant_connection(tenant_id) as conn:
        version: int = await conn.fetchval(
            "SELECT policy_version FROM cases WHERE id = $1", case_id
        )
    return version


async def test_a_tenant_without_a_policy_gets_the_built_in_version_0(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    resp = await client.get("/policy", headers=_auth(two_tenants["a"]["api_key"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 0
    assert body["created_by"] is None
    assert body["policy"]["auto_clear"] == {
        "min_combined": 0.85,
        "face_match_weight": 0.6,
        "extraction_weight": 0.4,
    }


async def test_put_creates_the_next_version_and_never_edits_one(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    headers = _auth(two_tenants["a"]["api_key"])
    first = await client.put("/policy", json=STRICT, headers=headers)
    second = await client.put("/policy", json={**STRICT, "min_face_match": 0.5}, headers=headers)
    assert (first.status_code, second.status_code) == (201, 201)
    assert (first.json()["version"], second.json()["version"]) == (1, 2)
    assert first.json()["policy"]["review_nationalities"] == ["PRK"]  # normalised on the way in

    versions = await client.get("/policy/versions", headers=headers)
    assert [v["version"] for v in versions.json()] == [2, 1, 0]
    assert versions.json()[1]["policy"]["min_face_match"] == 0.6  # v1 is unchanged by v2


async def test_an_invalid_policy_is_rejected_and_writes_nothing(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    headers = _auth(two_tenants["a"]["api_key"])
    bad_weights = {**STRICT, "auto_clear": {**STRICT["auto_clear"], "extraction_weight": 0.9}}
    for body in (bad_weights, {**STRICT, "on_sanctions_hit": "approve"}):
        assert (await client.put("/policy", json=body, headers=headers)).status_code == 422
    assert (await client.get("/policy", headers=headers)).json()["version"] == 0


async def test_policies_are_tenant_isolated(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    resp = await client.put("/policy", json=STRICT, headers=_auth(two_tenants["a"]["api_key"]))
    assert resp.status_code == 201
    other = await client.get("/policy", headers=_auth(two_tenants["b"]["api_key"]))
    assert other.json()["version"] == 0
    versions = await client.get("/policy/versions", headers=_auth(two_tenants["b"]["api_key"]))
    assert [v["version"] for v in versions.json()] == [0]


async def test_a_case_is_pinned_to_the_version_current_at_creation(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant = two_tenants["a"]
    before = await _create_case(client, tenant["api_key"])
    await client.put("/policy", json=STRICT, headers=_auth(tenant["api_key"]))
    after = await _create_case(client, tenant["api_key"])
    await client.put(
        "/policy", json={**STRICT, "min_face_match": 0.1}, headers=_auth(tenant["api_key"])
    )

    assert await _policy_version_of(tenant["tenant_id"], before) == 0
    assert await _policy_version_of(tenant["tenant_id"], after) == 1  # not 2


async def test_the_app_role_cannot_edit_or_delete_a_policy_version(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant = two_tenants["a"]
    await client.put("/policy", json=STRICT, headers=_auth(tenant["api_key"]))
    async with db.tenant_connection(tenant["tenant_id"]) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE risk_policies SET document = '{}' WHERE version = 1")
    async with db.tenant_connection(tenant["tenant_id"]) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM risk_policies WHERE version = 1")
