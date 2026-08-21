"""Tests for the audit_log hash chain (Phase 11): the shared hash formula, record()'s
concurrency safety, audited()'s entry/exit pairing, and the tamper-detection property the phase's
exit criteria calls for -- all against real Postgres, no mocked chain.
"""

import asyncio
import secrets
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import asyncpg
import pytest
import pytest_asyncio

from scripts.verify_audit_chain import main, verify_chain
from services.common.audit_hash import compute_row_hash
from services.pipeline import audit, db

_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _db_pool() -> AsyncIterator[None]:
    await db.init_pool()
    yield
    await db.close_pool()


async def _create_tenant_and_case() -> tuple[str, str]:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            suffix = secrets.token_hex(4)
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"Audit Test {suffix}",
                f"audit-test-{suffix}",
            )
            case_id = await conn.fetchval(
                "INSERT INTO cases (tenant_id, subject_name, status) VALUES ($1, $2, $3) "
                "RETURNING id",
                tenant_id,
                "Jane Doe",
                "needs_review",
            )
        return str(tenant_id), str(case_id)
    finally:
        await conn.close()


async def _delete_tenant(tenant_id: str) -> None:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM audit_log WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM cases WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
    finally:
        await conn.close()


async def _fetch_chain(tenant_id: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        rows: list[asyncpg.Record] = await conn.fetch(
            "SELECT id, tenant_id, case_id, event_type, actor, model_version, input_hash, "
            "payload, prev_row_hash, row_hash, created_at FROM audit_log "
            "WHERE tenant_id = $1 ORDER BY id ASC",
            tenant_id,
        )
        return rows
    finally:
        await conn.close()


@dataclass
class _HashArgs:
    """A real dataclass, not a raw dict of kwargs -- dataclasses.replace() below is typed as
    accepting **changes: Any, so overriding one field for the "changes anything" test doesn't
    hit the same "expanding a loosely-typed dict as **kwargs" issue compute_row_hash's own
    precisely-typed parameters would otherwise flag under mypy --strict."""

    tenant_id: str = "t1"
    case_id: str = "c1"
    event_type: str = "extract_document.completed"
    actor: str = "system:pipeline-worker"
    model_version: str | None = "gemini-2.0"
    input_hash: str | None = "abc123"
    payload_json: str = '{"confidence": 0.9}'
    created_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    prev_row_hash: str | None = None

    def hash(self) -> str:
        return compute_row_hash(
            tenant_id=self.tenant_id,
            case_id=self.case_id,
            event_type=self.event_type,
            actor=self.actor,
            model_version=self.model_version,
            input_hash=self.input_hash,
            payload_json=self.payload_json,
            created_at=self.created_at,
            prev_row_hash=self.prev_row_hash,
        )


def test_compute_row_hash_is_deterministic() -> None:
    args = _HashArgs()
    assert args.hash() == args.hash()


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("tenant_id", "t2"),
        ("case_id", "c2"),
        ("event_type", "extract_document.failed"),
        ("actor", "system:other-worker"),
        ("model_version", "gemini-3.0"),
        ("input_hash", "def456"),
        ("payload_json", '{"confidence": 0.1}'),
        ("prev_row_hash", "somehash"),
    ],
)
def test_compute_row_hash_changes_when_any_field_changes(field: str, new_value: str) -> None:
    base = _HashArgs()
    # mypy checks replace()'s **changes against every _HashArgs field, including created_at:
    # datetime -- it can't know from the dict's dict[str, str] type alone that "field" is never
    # "created_at" in this parametrization. It never is (see the cases list above).
    changed = replace(base, **{field: new_value})  # type: ignore[arg-type]
    assert base.hash() != changed.hash()


async def test_record_extends_the_chain_with_the_correct_prev_row_hash() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await audit.record(tenant_id, case_id, "step_one", actor="test")
        await audit.record(tenant_id, case_id, "step_two", actor="test")

        rows = await _fetch_chain(tenant_id)
        assert len(rows) == 2
        assert rows[0]["prev_row_hash"] is None
        assert rows[1]["prev_row_hash"] == rows[0]["row_hash"]
        assert verify_chain(rows) == []
    finally:
        await _delete_tenant(tenant_id)


async def test_concurrent_writes_for_the_same_tenant_never_fork_the_chain() -> None:
    # The real race this guards against: N activities for the same tenant (different cases can
    # process concurrently) all calling record() at once. Without the advisory lock in
    # audit.record(), two concurrent writers could both read the same "last" row and each
    # compute a row_hash chained to it -- this test fires 20 real concurrent writes and asserts
    # the result is one unbroken chain of 20, not a fork.
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await asyncio.gather(
            *[
                audit.record(tenant_id, case_id, f"concurrent_step_{i}", actor="test")
                for i in range(20)
            ]
        )

        rows = await _fetch_chain(tenant_id)
        assert len(rows) == 20
        assert verify_chain(rows) == []
        # A fork would show up as two rows sharing the same prev_row_hash -- an explicit extra
        # assertion on top of verify_chain(), which would also catch this via the "does not
        # match the preceding row" check, but this names the failure mode directly.
        prev_hashes = [r["prev_row_hash"] for r in rows]
        assert len(prev_hashes) == len(set(prev_hashes))
    finally:
        await _delete_tenant(tenant_id)


async def test_audited_records_started_and_completed_with_exit_info() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    # input_hash/row_hash/prev_row_hash are CHAR(64) (fixed-length, sized for a real sha256
    # hexdigest) -- a shorter value gets space-padded on read, so the test value has to actually
    # be hash-shaped, the same way every real caller's hashlib.sha256(...).hexdigest() is.
    fake_input_hash = "a" * 64
    try:
        async with audit.audited(tenant_id, case_id, "widget_step") as a:
            a.model_version = "widget-v1"
            a.input_hash = fake_input_hash
            a.payload = {"widgets_processed": 3}

        rows = await _fetch_chain(tenant_id)
        assert [r["event_type"] for r in rows] == ["widget_step.started", "widget_step.completed"]
        completed = rows[1]
        assert completed["model_version"] == "widget-v1"
        assert completed["input_hash"] == fake_input_hash
        assert completed["payload"] == '{"widgets_processed": 3}'
        assert verify_chain(rows) == []
    finally:
        await _delete_tenant(tenant_id)


async def test_audited_records_failed_on_exception_and_still_raises() -> None:
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        with pytest.raises(ValueError, match="boom"):
            async with audit.audited(tenant_id, case_id, "widget_step") as a:
                a.payload = {"in_progress": True}
                raise ValueError("boom")

        rows = await _fetch_chain(tenant_id)
        assert [r["event_type"] for r in rows] == ["widget_step.started", "widget_step.failed"]
        assert "boom" in rows[1]["payload"]
        assert verify_chain(rows) == []
    finally:
        await _delete_tenant(tenant_id)


async def test_verify_chain_detects_a_tampered_historical_row() -> None:
    """The Phase 11 exit criterion: manually editing a historical row breaks the
    chain-verification script."""
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await audit.record(tenant_id, case_id, "step_one", actor="test", payload={"n": 1})
        await audit.record(tenant_id, case_id, "step_two", actor="test", payload={"n": 2})
        await audit.record(tenant_id, case_id, "step_three", actor="test", payload={"n": 3})

        rows = await _fetch_chain(tenant_id)
        assert verify_chain(rows) == []  # intact before tampering

        # Simulates an attacker with direct DB access editing history -- sentrilog_app itself
        # has no UPDATE grant on audit_log (SELECT + INSERT only), so this uses the superuser
        # connection the same way test cleanup does, not the app's own path.
        tampered_id = rows[1]["id"]
        admin_conn = await asyncpg.connect(dsn=_ADMIN_DSN)
        try:
            await admin_conn.execute(
                "UPDATE audit_log SET payload = $1 WHERE id = $2",
                '{"n": 999}',
                tampered_id,
            )
        finally:
            await admin_conn.close()

        tampered_rows = await _fetch_chain(tenant_id)
        violations = verify_chain(tampered_rows)
        assert violations != []
        assert any(f"id={tampered_id}" in v for v in violations)
    finally:
        await _delete_tenant(tenant_id)


async def test_verify_chain_is_empty_for_no_rows() -> None:
    assert verify_chain([]) == []


async def test_cli_main_finds_real_rows_via_its_own_admin_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercises main() end-to-end, not just verify_chain() in isolation -- audit_log has RLS
    enabled, so a connection using sentrilog_app's own DSN with no tenant context set would see
    zero rows for every tenant (found for real while writing this: the script's first draft
    connected via that DSN and silently reported "0 row(s), chain OK" for a tenant that actually
    had 2). main() has to connect as the superuser role to see rows across tenants at all --
    this test would have caught that bug, verify_chain()-in-isolation tests above cannot.
    """
    tenant_id, case_id = await _create_tenant_and_case()
    try:
        await audit.record(tenant_id, case_id, "step_one", actor="test")
        await audit.record(tenant_id, case_id, "step_two", actor="test")

        monkeypatch.setattr(sys, "argv", ["verify_audit_chain.py", tenant_id])
        exit_code = await main()
        output = capsys.readouterr().out

        assert exit_code == 0
        assert f"tenant {tenant_id}: 2 row(s), chain OK" in output
    finally:
        await _delete_tenant(tenant_id)
