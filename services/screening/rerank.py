"""Second-stage reranker over vector-search candidates (improvements.md P-4, R8).

A bi-encoder embeds the query and each list name separately and compares the vectors; a
cross-encoder reads the two names together and scores the pair, which is slower but sees
interactions a single vector cannot -- token order, a shared surname against a different given
name. Run over the top few candidates only, it is affordable on CPU.

It is not assumed to help. The evaluation harness (services/eval/screening.py) measures recall and
false-positive rate with and without it, and RERANKER_ENABLED ships off unless that comparison
says otherwise.
"""

from collections.abc import Sequence
from typing import Protocol


class Reranker(Protocol):
    def score(self, query: str, candidates: Sequence[str]) -> list[float]:
        """One score per candidate, in input order. Higher means more likely the same name."""
        ...


class CrossEncoderReranker:
    def __init__(self, model: str, *, cache_dir: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model, cache_dir=cache_dir)
        self.model_name = model

    def score(self, query: str, candidates: Sequence[str]) -> list[float]:
        if not candidates:
            return []
        return [float(s) for s in self._model.rerank(query, list(candidates))]
