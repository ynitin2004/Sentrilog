"""The shipped screening configuration is the one the harness chose (Phase 13 exit criterion).

Compares the class defaults in services/screening/config.py -- not the loaded settings, which a
local .env can override -- against the committed result of `python -m services.eval screening
--compare --write-results`. Change one without the other and this fails.
"""

import json

from services.eval.datasets import RESULTS_DIR
from services.screening.config import Settings


def _default(name: str) -> object:
    return Settings.model_fields[name].default


def test_screening_defaults_match_the_harness_choice() -> None:
    chosen = json.loads((RESULTS_DIR / "screening.json").read_text(encoding="utf-8"))["chosen"]
    point = chosen["chosen"]
    provider, _, model = chosen["embedding"].partition(":")

    assert _default("embedding_provider") == provider
    if provider == "fastembed":
        assert _default("fastembed_model") == model
    assert _default("sanctions_vector_threshold") == point["vector_threshold"]
    assert _default("phonetic_enabled") == point["use_phonetic"]
    assert _default("reranker_enabled") == (point["rerank_threshold"] is not None)
    assert chosen["target_met"] is True
