"""Screens a name against the sanctions list via two independent methods, tuned for recall
per PLAN.md: vector similarity catches names that read differently but mean the same thing,
phonetic matching catches names that sound the same but embed differently. Run both rather
than picking one -- a hit either method would have missed on its own is exactly the failure
mode this design exists to avoid.

Phase 13 splits this in two: gather_candidates() asks Qdrant, decide_hits() applies the
thresholds. The evaluation harness calls the same decide_hits() over candidates gathered with no
threshold at all, so the configuration it measures is, line for line, the one screen_name() runs.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from .config import settings
from .embeddings import EmbeddingClient
from .names import normalize_name
from .phonetic import phonetic_codes
from .qdrant_store import search_by_phonetic_codes, search_by_vector
from .rerank import Reranker


@dataclass
class SanctionsHit:
    list_source: str
    matched_name: str
    match_score: float
    method: str  # "vector" | "phonetic"
    entity_id: str | None = None
    list_version: str | None = None
    rerank_score: float | None = None


@dataclass(frozen=True)
class Candidate:
    list_source: str
    name: str
    entity_id: str | None
    list_version: str | None
    vector_score: float | None  # None: found only by the phonetic query
    phonetic: bool
    rerank_score: float | None = None


@dataclass(frozen=True)
class ScreeningConfig:
    vector_threshold: float
    use_phonetic: bool = True
    rerank_threshold: float | None = None  # None: no reranker stage

    @classmethod
    def from_settings(cls) -> "ScreeningConfig":
        return cls(
            vector_threshold=settings.sanctions_vector_threshold,
            use_phonetic=settings.phonetic_enabled,
            rerank_threshold=settings.rerank_threshold if settings.reranker_enabled else None,
        )


def _candidate(payload: dict[str, Any], *, vector_score: float | None, phonetic: bool) -> Candidate:
    return Candidate(
        list_source=payload["list_source"],
        name=payload["name"],
        entity_id=payload.get("entity_id"),
        list_version=payload.get("list_version"),
        vector_score=vector_score,
        phonetic=phonetic,
    )


def gather_candidates(
    client: EmbeddingClient,
    normalized_name: str,
    *,
    retrieval_floor: float,
    limit: int,
    collection_name: str | None = None,
) -> list[Candidate]:
    by_key: dict[tuple[str, str | None, str], Candidate] = {}

    vector = client.embed(normalized_name)
    for point in search_by_vector(
        vector, limit=limit, score_threshold=retrieval_floor, collection_name=collection_name
    ):
        p = point.payload or {}
        key = (p["list_source"], p.get("entity_id"), p["name"])
        by_key[key] = _candidate(p, vector_score=point.score, phonetic=False)

    primary, secondary = phonetic_codes(normalized_name)
    codes = [c for c in (primary, secondary) if c]
    for record in search_by_phonetic_codes(codes, collection_name=collection_name):
        p = record.payload or {}
        key = (p["list_source"], p.get("entity_id"), p["name"])
        existing = by_key.get(key)
        if existing is not None:
            # Already surfaced by vector search -- one candidate, not one per method, so a
            # strong double-signal match doesn't look like two separate weaker hits.
            by_key[key] = replace(existing, phonetic=True)
        else:
            by_key[key] = _candidate(p, vector_score=None, phonetic=True)

    return list(by_key.values())


def rerank_candidates(
    reranker: Reranker, normalized_name: str, candidates: Sequence[Candidate]
) -> list[Candidate]:
    scores = reranker.score(normalized_name, [c.name for c in candidates])
    return [replace(c, rerank_score=s) for c, s in zip(candidates, scores, strict=True)]


def decide_hits(candidates: Sequence[Candidate], config: ScreeningConfig) -> list[SanctionsHit]:
    hits: list[SanctionsHit] = []
    for c in candidates:
        by_vector = c.vector_score is not None and c.vector_score >= config.vector_threshold
        by_phonetic = config.use_phonetic and c.phonetic
        if not (by_vector or by_phonetic):
            continue
        if config.rerank_threshold is not None:
            if c.rerank_score is None or c.rerank_score < config.rerank_threshold:
                continue
        hits.append(
            SanctionsHit(
                list_source=c.list_source,
                matched_name=c.name,
                # A phonetic match is a boolean code match, not a distance score.
                match_score=c.vector_score if by_vector and c.vector_score is not None else 1.0,
                method="vector" if by_vector else "phonetic",
                entity_id=c.entity_id,
                list_version=c.list_version,
                rerank_score=c.rerank_score,
            )
        )
    # Vector hits first, as before Phase 13, so callers that look at hits[0] see the strongest.
    hits.sort(key=lambda h: (h.method != "vector", -h.match_score))
    return hits


def screen_name(
    client: EmbeddingClient,
    name: str,
    *,
    collection_name: str | None = None,
    reranker: Reranker | None = None,
    config: ScreeningConfig | None = None,
) -> list[SanctionsHit]:
    config = config or ScreeningConfig.from_settings()
    normalized = normalize_name(name)
    if not normalized:
        return []
    candidates = gather_candidates(
        client,
        normalized,
        retrieval_floor=config.vector_threshold,
        limit=settings.vector_candidates,
        collection_name=collection_name,
    )
    if config.rerank_threshold is not None:
        if reranker is None:
            raise ValueError("config has a rerank threshold but no reranker was supplied")
        candidates = rerank_candidates(reranker, normalized, candidates)
    return decide_hits(candidates, config)
