"""The secrets provider wired in as a real pydantic-settings source (Phase 12).

Proves the two properties that actually matter once secrets stop being plain env vars: a
`secret=True` field resolves through the configured provider, and its value is masked anywhere
a Settings object gets serialized -- which this same phase made a live concern by adding JSON
structured logging and a console span exporter, both of which serialize whatever they're given.
"""

import json
from pathlib import Path

import pytest
from pydantic import Field, SecretStr

from services.common.secrets import reset_secrets_provider
from services.common.settings_base import ServiceSettings, secret_field_names


class _ExampleSettings(ServiceSettings):
    some_api_key: SecretStr = Field(json_schema_extra={"secret": True})
    plain_value: str = "visible-default"


@pytest.fixture(autouse=True)
def _reset_provider() -> None:
    reset_secrets_provider()


def test_secret_field_resolves_through_the_file_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"some_api_key": "resolved-from-file"}), encoding="utf-8")
    monkeypatch.setenv("SENTRILOG_SECRETS_PROVIDER", "file")
    monkeypatch.setenv("SENTRILOG_SECRETS_FILE", str(secrets_file))

    settings = _ExampleSettings()  # type: ignore[call-arg]
    assert settings.some_api_key.get_secret_value() == "resolved-from-file"


def test_provider_wins_over_a_stale_environment_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Source ordering is deliberate: a host that still has an old exported value must not
    # silently override what the configured secrets provider says is current.
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"some_api_key": "current"}), encoding="utf-8")
    monkeypatch.setenv("SENTRILOG_SECRETS_PROVIDER", "file")
    monkeypatch.setenv("SENTRILOG_SECRETS_FILE", str(secrets_file))
    monkeypatch.setenv("SOME_API_KEY", "stale-leftover")

    settings = _ExampleSettings()  # type: ignore[call-arg]
    assert settings.some_api_key.get_secret_value() == "current"


def test_env_provider_still_works_so_local_dev_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENTRILOG_SECRETS_PROVIDER", raising=False)
    monkeypatch.setenv("SOME_API_KEY", "from-env")
    settings = _ExampleSettings()  # type: ignore[call-arg]
    assert settings.some_api_key.get_secret_value() == "from-env"


def test_secret_value_is_masked_in_repr_and_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOME_API_KEY", "sk-live-should-never-be-logged")
    settings = _ExampleSettings()  # type: ignore[call-arg]

    assert "sk-live-should-never-be-logged" not in repr(settings)
    assert "sk-live-should-never-be-logged" not in str(settings)
    assert "sk-live-should-never-be-logged" not in json.dumps(settings.model_dump(mode="json"))
    # The non-secret field stays visible on purpose -- masking everything would make the
    # structured logs this phase added useless for debugging configuration.
    assert "visible-default" in repr(settings)


def test_real_service_settings_declare_their_secrets() -> None:
    from services.intake.config import Settings as IntakeSettings
    from services.pipeline.config import Settings as PipelineSettings
    from services.screening.config import Settings as ScreeningSettings

    assert set(secret_field_names(IntakeSettings)) == {"database_url", "s3_secret_key"}
    assert set(secret_field_names(PipelineSettings)) == {
        "gemini_api_key",
        "database_url",
        "s3_secret_key",
    }
    assert set(secret_field_names(ScreeningSettings)) == {"database_url", "gemini_api_key"}
