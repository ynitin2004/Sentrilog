"""Tests for the secrets provider seam (Phase 12).

These are genuine unit tests with no external dependency -- the point of the seam is that the
*provider* is swappable, so exercising it needs an environment and a temp file, not a database.
What's being proven is the behaviour a real AWS Secrets Manager provider would have to match:
resolve by name, cache, and fail loudly rather than return an empty default.
"""

import json
from pathlib import Path

import pytest

from services.common.secrets import (
    EnvSecretsProvider,
    JsonFileSecretsProvider,
    SecretNotFoundError,
    get_secrets_provider,
    reset_secrets_provider,
)


@pytest.fixture(autouse=True)
def _reset_provider() -> None:
    reset_secrets_provider()


def test_env_provider_resolves_uppercase_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_API_KEY", "sk-live-123")
    assert EnvSecretsProvider().get("some_api_key") == "sk-live-123"


def test_env_provider_raises_rather_than_returning_a_default() -> None:
    with pytest.raises(SecretNotFoundError, match="definitely_missing_secret"):
        EnvSecretsProvider().get("definitely_missing_secret")


def test_env_provider_caches_so_a_later_env_change_is_not_re_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Caching is the behaviour that matters for a real provider: a Secrets Manager lookup is a
    # billed, throttled network call, so resolving the same name twice must not re-fetch.
    monkeypatch.setenv("CACHED_SECRET", "first")
    provider = EnvSecretsProvider()
    assert provider.get("cached_secret") == "first"
    monkeypatch.setenv("CACHED_SECRET", "second")
    assert provider.get("cached_secret") == "first"


def test_file_provider_reads_a_json_blob(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"GEMINI_API_KEY": "from-file"}), encoding="utf-8")
    assert JsonFileSecretsProvider(secrets_file).get("gemini_api_key") == "from-file"


def test_file_provider_missing_file_names_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(SecretNotFoundError, match="nope.json"):
        JsonFileSecretsProvider(missing).get("anything")


def test_file_provider_missing_key_names_the_secret(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"OTHER": "x"}), encoding="utf-8")
    with pytest.raises(SecretNotFoundError, match="gemini_api_key"):
        JsonFileSecretsProvider(secrets_file).get("gemini_api_key")


def test_provider_selection_defaults_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRILOG_SECRETS_PROVIDER", raising=False)
    assert isinstance(get_secrets_provider(), EnvSecretsProvider)


def test_provider_selection_file_requires_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRILOG_SECRETS_PROVIDER", "file")
    monkeypatch.delenv("SENTRILOG_SECRETS_FILE", raising=False)
    with pytest.raises(SecretNotFoundError, match="SENTRILOG_SECRETS_FILE"):
        get_secrets_provider()


def test_provider_selection_rejects_an_unknown_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRILOG_SECRETS_PROVIDER", "vault")
    with pytest.raises(SecretNotFoundError, match="vault"):
        get_secrets_provider()


def test_file_provider_is_selected_and_used_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"some_api_key": "via-file-provider"}), encoding="utf-8")
    monkeypatch.setenv("SENTRILOG_SECRETS_PROVIDER", "file")
    monkeypatch.setenv("SENTRILOG_SECRETS_FILE", str(secrets_file))

    provider = get_secrets_provider()
    assert isinstance(provider, JsonFileSecretsProvider)
    assert provider.get("some_api_key") == "via-file-provider"
