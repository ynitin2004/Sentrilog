"""List snapshots against real Qdrant and Postgres: a snapshot is built, recorded and activated
through an alias, hits carry its list_version, and rolling back is repointing the alias with
nothing deleted (improvements.md P-4; STUDY.md, Phase 13).

Every test uses its own alias and collections, so the shared dev alias ingest.py manages is
never touched.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import asyncpg
import pytest

from services.screening import snapshots
from services.screening.config import settings
from services.screening.embeddings import CharNgramEmbeddingClient
from services.screening.ingest import ingest_entries
from services.screening.qdrant_store import drop_collection, get_qdrant_client, resolve_alias
from services.screening.screen import ScreeningConfig, screen_name
from services.screening.snapshots import SourceEntry

_CLIENT = CharNgramEmbeddingClient(256)
_ADMIN_DSN = "postgresql://sentrilog:sentrilog@localhost:5432/sentrilog"


@pytest.fixture
def isolated_alias(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    alias = f"test-alias-{uuid.uuid4()}"
    monkeypatch.setattr(settings, "qdrant_collection", alias)
    created: list[str] = []
    yield created
    client = get_qdrant_client()
    if resolve_alias(alias) is not None:
        from qdrant_client import models

        client.update_collection_aliases(
            change_aliases_operations=[
                models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias))
            ]
        )
    for name in created:
        drop_collection(name)


def _ingest(names: list[tuple[str, str]], source: str, created: list[str]) -> snapshots.Snapshot:
    snapshot = ingest_entries(
        _CLIENT,
        [SourceEntry(entity_id=e, name=n) for e, n in names],
        source=source,
        content_hash=uuid.uuid4().hex * 2,
        embedding_model=f"test-{uuid.uuid4().hex[:8]}",
        fetched_at=datetime.now(UTC),
    )
    created.append(snapshot.collection_name)
    return snapshot


def test_duplicate_spellings_of_one_entity_collapse_but_namesakes_do_not(
    isolated_alias: list[str],
) -> None:
    snapshot = _ingest(
        [("1", "PETROV, Aleksandr"), ("1", "Aleksandr PETROV"), ("2", "Aleksandr Petrov")],
        "TEST",
        isolated_alias,
    )
    # Entity 1's two forms normalise to one string; entity 2 is a different listed person who
    # happens to share the name, and must stay a separate point.
    assert snapshot.entry_count == 2


def test_hits_carry_the_list_version_of_the_active_snapshot(isolated_alias: list[str]) -> None:
    snapshot = _ingest([("7", "SOKOLOV, Viktor Ivanovich")], "TEST", isolated_alias)
    snapshots.activate(snapshot.collection_name)

    hits = screen_name(_CLIENT, "Viktor Ivanovich Sokolov", config=ScreeningConfig(0.9))
    assert len(hits) == 1
    assert hits[0].entity_id == "7"
    assert hits[0].list_version == snapshot.list_version


def test_rollback_repoints_the_alias_and_deletes_nothing(isolated_alias: list[str]) -> None:
    good = _ingest([("7", "SOKOLOV, Viktor")], "TEST", isolated_alias)
    snapshots.activate(good.collection_name)
    bad = _ingest([("8", "NOBODY, Listed")], "TEST", isolated_alias)
    snapshots.activate(bad.collection_name)
    assert screen_name(_CLIENT, "Viktor Sokolov", config=ScreeningConfig(0.9)) == []

    snapshots.activate(good.collection_name)  # the rollback

    assert resolve_alias(settings.qdrant_collection) == good.collection_name
    assert get_qdrant_client().collection_exists(bad.collection_name)  # kept, for the audit
    hits = screen_name(_CLIENT, "Viktor Sokolov", config=ScreeningConfig(0.9))
    assert [h.list_version for h in hits] == [good.list_version]


async def test_snapshot_rows_are_insert_only_for_the_app_role(isolated_alias: list[str]) -> None:
    snapshot = _ingest([("7", "SOKOLOV, Viktor")], "TEST", isolated_alias)
    app = await asyncpg.connect(settings.database_url.get_secret_value())
    try:
        await snapshots.record_snapshot(app, snapshot)
        snapshots.activate(snapshot.collection_name)
        assert await snapshots.active_list_version(app) == snapshot.list_version
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute(
                "UPDATE list_snapshots SET entry_count = 0 WHERE collection_name = $1",
                snapshot.collection_name,
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute(
                "DELETE FROM list_snapshots WHERE collection_name = $1", snapshot.collection_name
            )
    finally:
        await app.close()
        admin = await asyncpg.connect(_ADMIN_DSN)
        try:
            await admin.execute(
                "DELETE FROM list_snapshots WHERE collection_name = $1", snapshot.collection_name
            )
        finally:
            await admin.close()
