import secrets

import asyncpg
import httpx
import pytest
from httpx import AsyncClient

_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"

VALID_PAYLOAD = {
    "subject_name": "Alice Test",
    "subject_dob": "1990-01-01",
    "id_document": {"content_type": "image/jpeg", "size_bytes": 50_000},
    "selfie": {"content_type": "image/jpeg", "size_bytes": 30_000},
}


def _auth(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def test_create_case_returns_tenant_scoped_upload_targets(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    resp = await client.post(
        "/cases",
        json=VALID_PAYLOAD,
        headers={**_auth(tenant_a["api_key"]), "Idempotency-Key": secrets.token_hex(8)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    # s3_key must be namespaced under the authenticated tenant's own id, not derivable from
    # anything the client supplied -- this is what prevents one tenant's upload from ever
    # landing under another tenant's prefix.
    assert body["id_document"]["s3_key"].startswith(f"{tenant_a['tenant_id']}/")
    assert body["selfie"]["s3_key"].startswith(f"{tenant_a['tenant_id']}/")
    assert "X-Amz-Signature" in body["id_document"]["upload_url"]


async def test_idempotency_key_replay_returns_same_case(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    idem_key = secrets.token_hex(8)
    headers = {**_auth(tenant_a["api_key"]), "Idempotency-Key": idem_key}

    first = await client.post("/cases", json=VALID_PAYLOAD, headers=headers)
    second = await client.post("/cases", json=VALID_PAYLOAD, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["case_id"] == second.json()["case_id"]
    assert first.json()["id_document"]["s3_key"] == second.json()["id_document"]["s3_key"]


async def test_idempotency_key_is_scoped_per_tenant_not_global(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    """The same idempotency key from two different tenants must create two independent
    cases -- the uniqueness constraint is (tenant_id, idempotency_key), not idempotency_key
    alone, and this is the one test that would catch a regression to a global constraint."""
    idem_key = secrets.token_hex(8)
    resp_a = await client.post(
        "/cases",
        json=VALID_PAYLOAD,
        headers={**_auth(two_tenants["a"]["api_key"]), "Idempotency-Key": idem_key},
    )
    resp_b = await client.post(
        "/cases",
        json=VALID_PAYLOAD,
        headers={**_auth(two_tenants["b"]["api_key"]), "Idempotency-Key": idem_key},
    )
    assert resp_a.status_code == 201
    assert resp_b.status_code == 201
    assert resp_a.json()["case_id"] != resp_b.json()["case_id"]


async def test_upload_flow_end_to_end_via_presigned_url(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    resp = await client.post(
        "/cases",
        json=VALID_PAYLOAD,
        headers={**_auth(tenant_a["api_key"]), "Idempotency-Key": secrets.token_hex(8)},
    )
    upload_url = resp.json()["id_document"]["upload_url"]

    async with httpx.AsyncClient() as raw:
        put_resp = await raw.put(
            upload_url, content=b"fake-jpeg-bytes", headers={"Content-Type": "image/jpeg"}
        )
    assert put_resp.status_code == 200


async def test_cross_tenant_get_returns_404_not_403(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a, tenant_b = two_tenants["a"], two_tenants["b"]
    created = await client.post(
        "/cases",
        json=VALID_PAYLOAD,
        headers={**_auth(tenant_a["api_key"]), "Idempotency-Key": secrets.token_hex(8)},
    )
    case_id = created.json()["case_id"]

    own = await client.get(f"/cases/{case_id}", headers=_auth(tenant_a["api_key"]))
    assert own.status_code == 200

    # 404, not 403: tenant B should not be able to tell the case even exists.
    other = await client.get(f"/cases/{case_id}", headers=_auth(tenant_b["api_key"]))
    assert other.status_code == 404


async def test_missing_auth_header_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/cases/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


async def test_invalid_api_key_returns_401(client: AsyncClient) -> None:
    resp = await client.get(
        "/cases/00000000-0000-0000-0000-000000000000",
        headers=_auth("this-key-does-not-exist"),
    )
    assert resp.status_code == 401


async def test_malformed_case_id_returns_404_not_500(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    resp = await client.get("/cases/not-a-uuid", headers=_auth(two_tenants["a"]["api_key"]))
    assert resp.status_code == 404


async def test_invalid_content_type_returns_422(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    bad_payload = {
        **VALID_PAYLOAD,
        "id_document": {"content_type": "application/exe", "size_bytes": 100},
    }
    resp = await client.post(
        "/cases",
        json=bad_payload,
        headers={**_auth(two_tenants["a"]["api_key"]), "Idempotency-Key": secrets.token_hex(8)},
    )
    assert resp.status_code == 422


async def test_oversized_upload_rejected(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    bad_payload = {
        **VALID_PAYLOAD,
        "id_document": {"content_type": "image/jpeg", "size_bytes": 999_999_999},
    }
    resp = await client.post(
        "/cases",
        json=bad_payload,
        headers={**_auth(two_tenants["a"]["api_key"]), "Idempotency-Key": secrets.token_hex(8)},
    )
    assert resp.status_code == 422


async def test_rate_limit_returns_429_with_retry_after(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    from services.intake.config import settings

    headers = _auth(two_tenants["a"]["api_key"])
    case_id = "00000000-0000-0000-0000-000000000000"

    last_resp = None
    for _ in range(settings.rate_limit_requests + 5):
        last_resp = await client.get(f"/cases/{case_id}", headers=headers)
        if last_resp.status_code == 429:
            break

    assert last_resp is not None
    assert last_resp.status_code == 429
    assert "retry-after" in last_resp.headers


@pytest.mark.parametrize(
    # "" is deliberately excluded: GET /cases/ (empty path segment) hits Starlette's
    # trailing-slash redirect to /cases before routing ever reaches this handler -- that's
    # routing behavior, not a case-id-validation input this endpoint can see.
    "bad_uuid",
    ["  ", "'; DROP TABLE cases; --"],
)
async def test_malformed_case_id_never_500s(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]], bad_uuid: str
) -> None:
    resp = await client.get(f"/cases/{bad_uuid}", headers=_auth(two_tenants["a"]["api_key"]))
    assert resp.status_code in (404, 429)


async def _fetch_audit_event_types(tenant_id: str, case_id: str) -> list[str]:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        rows = await conn.fetch(
            "SELECT event_type FROM audit_log WHERE tenant_id = $1 AND case_id = $2 "
            "ORDER BY id ASC",
            tenant_id,
            case_id,
        )
        return [r["event_type"] for r in rows]
    finally:
        await conn.close()


async def test_create_case_writes_a_case_created_audit_row(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    resp = await client.post(
        "/cases",
        json=VALID_PAYLOAD,
        headers={**_auth(tenant_a["api_key"]), "Idempotency-Key": secrets.token_hex(8)},
    )
    assert resp.status_code == 201
    case_id = resp.json()["case_id"]
    assert await _fetch_audit_event_types(tenant_a["tenant_id"], case_id) == ["case_created"]


async def test_idempotency_key_replay_does_not_duplicate_the_audit_row(
    client: AsyncClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    tenant_a = two_tenants["a"]
    idem_key = secrets.token_hex(8)
    headers = {**_auth(tenant_a["api_key"]), "Idempotency-Key": idem_key}

    first = await client.post("/cases", json=VALID_PAYLOAD, headers=headers)
    second = await client.post("/cases", json=VALID_PAYLOAD, headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    case_id = first.json()["case_id"]

    # A replay didn't actually create the case again -- it must not look like it did in the
    # audit trail either.
    assert await _fetch_audit_event_types(tenant_a["tenant_id"], case_id) == ["case_created"]
