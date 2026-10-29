"""The base Settings class all three services build on, wiring the secrets provider
(services/common/secrets.py) in as a real pydantic-settings source.

Doing it as a *source* rather than bypassing pydantic matters: fields keep their declared types,
validation, and precedence rules, and a secret field is declared the same way any other field is
-- it just carries `secret=True` and resolves through the provider instead of straight off the
environment.

Secret fields are typed SecretStr, so the value is masked in reprs, tracebacks, and anything
that serializes a Settings object. That stopped being theoretical in this same phase: Phase 12
added JSON structured logging and a console span exporter, both of which happily serialize
whatever object they're handed.
"""

from collections.abc import Iterator
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from .secrets import SecretNotFoundError, get_secrets_provider


def is_secret_field(field: FieldInfo) -> bool:
    extra = field.json_schema_extra
    return isinstance(extra, dict) and bool(extra.get("secret"))


class SecretsProviderSource(PydanticBaseSettingsSource):
    """Resolves every field marked `secret=True` through the configured SecretsProvider."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        if not is_secret_field(field):
            return None, field_name, False
        try:
            return get_secrets_provider().get(field_name), field_name, False
        except SecretNotFoundError:
            # Not fatal here: a missing secret falls through to the env/.env sources below, and
            # pydantic raises its own "field required" error if nothing supplies it. That keeps
            # the local developer experience (plain .env, no secrets file) working unchanged.
            return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, is_complex = self.get_field_value(field, field_name)
            if value is not None:
                resolved[key] = self.prepare_field_value(field_name, field, value, is_complex)
        return resolved


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Ahead of env/.env so a configured secrets provider wins over a stale environment
        # variable left behind on the host -- but after init_settings, so tests can still
        # construct Settings(...) with explicit values.
        return (
            init_settings,
            SecretsProviderSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


def secret_field_names(settings_cls: type[BaseSettings]) -> Iterator[str]:
    for name, field in settings_cls.model_fields.items():
        if is_secret_field(field):
            yield name
