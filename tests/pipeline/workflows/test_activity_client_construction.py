"""Importing activities.py must not construct any third-party client.

This is a regression test for a CI failure, and it runs in a subprocess because it cannot be
written any other way: by the time any test in this suite executes, `activities` has already
been imported by something, so the module-level work under test has already happened. A fresh
interpreter is the only place "what does importing this cost?" is still an answerable question.

The failure it pins down: `_vlm_client` was built at module scope, and `genai.Client(api_key="")`
raises ValueError immediately rather than deferring the complaint to the first call. On CI,
where GEMINI_API_KEY is empty unless a repository secret is set, that took out four unrelated
test modules at *collection* -- webhook delivery, risk scoring and finalize, workflow control
flow, and the review end-to-end suite, none of which call Gemini. Locally it was invisible,
because a developer's .env has a real key.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

# One entry per client activities.py holds. Each names the activity that legitimately needs it,
# because that is the test's actual claim: a worker serving only the other activities should
# start without this dependency configured at all.
_CLIENTS = [
    pytest.param("GEMINI_API_KEY", "extract_id_document_activity", id="gemini-vlm"),
]


def _import_activities_with(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    import os

    env = {**os.environ, **env_overrides}
    # Tracing off: a span exporter flushing from an atexit hook writes to stdout during
    # interpreter shutdown, which would mix into the output this test reads on failure.
    env["OTEL_TRACES_EXPORTER"] = "none"
    return subprocess.run(
        [sys.executable, "-c", "import services.pipeline.workflows.activities"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.parametrize(("env_var", "owning_activity"), _CLIENTS)
def test_import_succeeds_without_the_credential(env_var: str, owning_activity: str) -> None:
    result = _import_activities_with({env_var: ""})

    assert result.returncode == 0, (
        f"importing activities.py required {env_var}, so every activity module that imports it "
        f"fails even though only {owning_activity} uses that credential.\n"
        f"stderr:\n{result.stderr}"
    )


def test_import_succeeds_with_every_credential_present() -> None:
    """The control. Without it, a test that broke the import outright would still pass above
    for the wrong reason only if it also broke it here -- so this is what distinguishes
    "lazy" from "quietly swallowing an error".
    """
    result = _import_activities_with({})

    assert result.returncode == 0, f"activities.py does not import at all:\n{result.stderr}"
