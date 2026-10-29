"""Secret resolution behind a provider seam, so no service ever reads a secret out of os.environ
directly (Phase 12, PLAN.md §9).

The point isn't that the local provider does anything clever -- it reads the same environment
variables the previous pydantic-settings code did. The point is the *seam*: application code
asks for a secret by name, a provider decides where that comes from, and swapping in a real AWS
Secrets Manager client in Phase 13 is one new class plus one env var, not an edit to every
config module and call site.

Two providers ship here:

- EnvSecretsProvider (default): the value of the matching environment variable, which
  pydantic-settings already populates from .env locally.
- JsonFileSecretsProvider: a JSON object of name -> value read off disk. This is what real
  deployments actually look like -- AWS Secrets Manager returns a JSON blob per secret, and
  ECS/EKS inject secrets as a mounted file rather than as environment variables (environment
  variables leak: they're visible in `ps`, inherited by every child process, and routinely
  dumped by crash reporters).

Both cache: a real Secrets Manager lookup is a network call that's billed and throttled per
request, so resolving the same secret repeatedly at import time must not re-fetch.
"""

import json
import os
from pathlib import Path
from typing import Protocol


class SecretNotFoundError(RuntimeError):
    """Raised instead of returning a default. A silently-empty API key or database password
    fails later, somewhere unrelated, with an error that doesn't name the real cause."""


class SecretsProvider(Protocol):
    def get(self, name: str) -> str: ...


class EnvSecretsProvider:
    """Environment variables, matched case-insensitively (SENTRILOG secrets are declared in
    lowercase by the Settings classes; the environment holds them uppercase)."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]

        value = os.environ.get(name.upper()) or os.environ.get(name)
        if value is None:
            raise SecretNotFoundError(
                f"secret {name!r} not found in the environment "
                f"(looked for {name.upper()} and {name})"
            )
        self._cache[name] = value
        return value


class JsonFileSecretsProvider:
    """A JSON object of secret-name -> value, read once off disk.

    Mirrors how a real deployment receives secrets: AWS Secrets Manager stores a JSON blob per
    secret and ECS/EKS mount it as a file, so the shape an app parses here is the shape it
    would parse in production -- only the source of the file changes.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._values: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._values is None:
            if not self._path.is_file():
                raise SecretNotFoundError(f"secrets file not found: {self._path}")
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            # Normalized to lowercase so a secrets file written with SCREAMING_CASE keys (the
            # convention when the same values also exist as env vars) resolves identically.
            self._values = {str(k).lower(): str(v) for k, v in raw.items()}
        return self._values

    def get(self, name: str) -> str:
        values = self._load()
        try:
            return values[name.lower()]
        except KeyError as exc:
            raise SecretNotFoundError(
                f"secret {name!r} not present in secrets file {self._path}"
            ) from exc


_PROVIDER_ENV_VAR = "SENTRILOG_SECRETS_PROVIDER"
_SECRETS_FILE_ENV_VAR = "SENTRILOG_SECRETS_FILE"

_provider: SecretsProvider | None = None


def get_secrets_provider() -> SecretsProvider:
    """Chosen once per process by SENTRILOG_SECRETS_PROVIDER ("env", the default, or "file").
    Deliberately a module-level singleton: providers cache, and a per-Settings-instance provider
    would re-fetch every secret for each of the three services' Settings objects."""
    global _provider
    if _provider is None:
        kind = os.environ.get(_PROVIDER_ENV_VAR, "env").strip().lower()
        if kind == "env":
            _provider = EnvSecretsProvider()
        elif kind == "file":
            path = os.environ.get(_SECRETS_FILE_ENV_VAR)
            if not path:
                raise SecretNotFoundError(
                    f"{_PROVIDER_ENV_VAR}=file requires {_SECRETS_FILE_ENV_VAR} to point at a "
                    "JSON secrets file"
                )
            _provider = JsonFileSecretsProvider(Path(path))
        else:
            raise SecretNotFoundError(
                f"unknown {_PROVIDER_ENV_VAR}={kind!r}; expected 'env' or 'file'"
            )
    return _provider


def reset_secrets_provider() -> None:
    """Drops the cached provider. For tests that switch providers mid-process -- nothing in the
    running services calls this."""
    global _provider
    _provider = None
