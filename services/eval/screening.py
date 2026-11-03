"""Screening evaluation: recall and false-positive rate over the labelled query set, and the
choice of the shipped threshold (improvements.md P-2, P-4; Phase 13 exit criteria).

How it measures. Each query goes through the production gather_candidates() with no threshold
at all, so every candidate the vector and phonetic searches would ever return is kept with its
score. Thresholds are then swept offline: for one query, whether it raises an alert is
monotone in the threshold, so the whole curve costs one pass over the candidates. The operating
point it picks is re-scored with the production decide_hits() as a consistency check -- the
number reported is the number screen_name() produces, not a re-implementation of it.

How it chooses. The target recall is fixed before looking at results (TARGET_RECALL). Among
every (embedding, threshold, phonetic on/off, reranker threshold) combination that reaches it,
the one with the lowest false-positive rate wins. Missing a listed person is a legal failure;
a false positive costs a reviewer minutes (STUDY.md, Phase 13).

    uv run python -m services.eval screening              # the shipped configuration
    uv run python -m services.eval screening --compare    # every candidate, and choose
"""

import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from services.screening.config import settings
from services.screening.embeddings import (
    CharNgramEmbeddingClient,
    EmbeddingClient,
    FastEmbedClient,
)
from services.screening.ofac import LIST_SOURCE, content_hash, parse_sdn, read_from_dir
from services.screening.qdrant_store import count_points, drop_collection, get_qdrant_client
from services.screening.rerank import CrossEncoderReranker, Reranker
from services.screening.screen import (
    Candidate,
    ScreeningConfig,
    decide_hits,
    gather_candidates,
    rerank_candidates,
)
from services.screening.snapshots import SourceEntry, build_collection, dedupe, slug

from .datasets import read_jsonl, verify_files
from .screening_data import DATASET_PATH, GENERATOR_VERSION, LIST_DIR

DATASET_SHA256 = "5ef15c4e3c973a25e4e433babfb98791ac078e7dee1e9f93e4cb231d4488fada"
TARGET_RECALL = 0.95
THRESHOLDS = np.round(np.arange(0.30, 1.0001, 0.01), 2)


@dataclass(frozen=True)
class EmbeddingChoice:
    provider: str  # "fastembed" | "ngram"
    model: str
    dimensions: int

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


CANDIDATES: list[EmbeddingChoice] = [
    EmbeddingChoice("ngram", CharNgramEmbeddingClient.model_name, 1024),
    EmbeddingChoice(
        "fastembed", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384
    ),
    EmbeddingChoice("fastembed", "sentence-transformers/all-MiniLM-L6-v2", 384),
    EmbeddingChoice("fastembed", "BAAI/bge-small-en-v1.5", 384),
]


def shipped_choice() -> EmbeddingChoice:
    if settings.embedding_provider == "ngram":
        return EmbeddingChoice(
            "ngram", CharNgramEmbeddingClient.model_name, settings.embedding_dimensions
        )
    if settings.embedding_provider == "fastembed":
        return EmbeddingChoice("fastembed", settings.fastembed_model, settings.embedding_dimensions)
    raise ValueError("the harness does not evaluate the Gemini provider (D26: rate limits)")


def build_client(choice: EmbeddingChoice) -> EmbeddingClient:
    if choice.provider == "ngram":
        return CharNgramEmbeddingClient(choice.dimensions)
    return FastEmbedClient(choice.model, cache_dir=settings.model_cache_dir)


@dataclass
class QueryResult:
    row: dict[str, Any]
    candidates: list[Candidate]


@dataclass
class OperatingPoint:
    vector_threshold: float
    use_phonetic: bool
    rerank_threshold: float | None
    recall: float
    false_positive_rate: float


@dataclass
class ModelResult:
    embedding: str
    reranker: str | None
    chosen: OperatingPoint
    target_met: bool
    recall_by_transform: dict[str, float]
    fpr_by_kind: dict[str, float]
    curve: list[dict[str, float]]
    ms_per_screen: float
    consistency_checked: bool
    extra: dict[str, Any] = field(default_factory=dict)


def load_queries() -> list[dict[str, Any]]:
    return read_jsonl(DATASET_PATH, expected_sha256=DATASET_SHA256)


def list_entries() -> tuple[list[SourceEntry], str]:
    verify_files(LIST_DIR)
    sdn, alt = read_from_dir(LIST_DIR)
    entries = [SourceEntry(e.entity_id, e.name) for e in parse_sdn(sdn, alt)]
    return entries, content_hash(sdn, alt)


def eval_collection(
    choice: EmbeddingChoice, client: EmbeddingClient, entries: Sequence[SourceEntry], list_hash: str
) -> str:
    """One collection per (list, embedding), reused across runs when its point count is right --
    rebuilding a 16,000-name index on every local iteration would dominate the run."""
    name = f"eval-{list_hash[:10]}-{slug(choice.model.rsplit('/', 1)[-1])}-{choice.dimensions}"
    expected = len(dedupe(entries))
    if get_qdrant_client().collection_exists(name):
        if count_points(name) == expected:
            return name
        drop_collection(name)
    build_collection(
        client,
        entries,
        source=LIST_SOURCE,
        list_version=f"eval-{list_hash[:10]}",
        collection_name=name,
    )
    return name


def gather_all(
    client: EmbeddingClient, collection: str, queries: Sequence[dict[str, Any]]
) -> tuple[list[QueryResult], float]:
    results: list[QueryResult] = []
    started = time.perf_counter()
    for row in queries:
        # retrieval_floor=-1.0: cosine similarity is never lower, so nothing is filtered here and
        # the sweep below sees every candidate the top-k search returns.
        candidates = gather_candidates(
            client,
            row["query"],
            retrieval_floor=-1.0,
            limit=settings.vector_candidates,
            collection_name=collection,
        )
        results.append(QueryResult(row, candidates))
    elapsed_ms = (time.perf_counter() - started) * 1000
    return results, elapsed_ms / max(len(queries), 1)


def _alert_level(
    result: QueryResult, *, use_phonetic: bool, rerank_threshold: float | None
) -> float:
    """The highest vector threshold at which this query still raises a qualifying hit.

    For a positive, only a hit on the right entity qualifies; for a negative, any hit is a
    false positive. A phonetic match qualifies at every threshold, hence +inf.
    """
    best = float("-inf")
    truth = result.row["entity_id"]
    for c in result.candidates:
        if truth is not None and c.entity_id != truth:
            continue
        if rerank_threshold is not None and (
            c.rerank_score is None or c.rerank_score < rerank_threshold
        ):
            continue
        if use_phonetic and c.phonetic:
            return float("inf")
        if c.vector_score is not None:
            best = max(best, c.vector_score)
    return best


def _sweep(
    results: Sequence[QueryResult], *, use_phonetic: bool, rerank_threshold: float | None
) -> tuple[np.ndarray, np.ndarray]:
    """recall and false-positive rate at every threshold in THRESHOLDS."""
    levels = np.array(
        [
            _alert_level(r, use_phonetic=use_phonetic, rerank_threshold=rerank_threshold)
            for r in results
        ]
    )
    positive = np.array([r.row["kind"] == "positive" for r in results])
    hit = levels[:, None] >= THRESHOLDS[None, :]
    return hit[positive].mean(axis=0), hit[~positive].mean(axis=0)


def _rate(results: Sequence[QueryResult], config: ScreeningConfig, kinds: set[str]) -> float:
    """Scored with the production decide_hits(), not the sweep."""
    selected = [r for r in results if r.row["kind"] in kinds]
    if not selected:
        return 0.0
    count = 0
    for r in selected:
        hits = decide_hits(r.candidates, config)
        if r.row["entity_id"] is None:
            count += bool(hits)
        else:
            count += any(h.entity_id == r.row["entity_id"] for h in hits)
    return count / len(selected)


def choose(
    results: Sequence[QueryResult],
    rerank_thresholds: Sequence[float | None],
    *,
    phonetic_options: Sequence[bool] = (True, False),
) -> tuple[OperatingPoint, bool]:
    best: OperatingPoint | None = None
    fallback: OperatingPoint | None = None
    for use_phonetic in phonetic_options:
        for r in rerank_thresholds:
            recall, fpr = _sweep(results, use_phonetic=use_phonetic, rerank_threshold=r)
            for i, t in enumerate(THRESHOLDS):
                point = OperatingPoint(float(t), use_phonetic, r, float(recall[i]), float(fpr[i]))
                if fallback is None or (point.recall, -point.false_positive_rate) > (
                    fallback.recall,
                    -fallback.false_positive_rate,
                ):
                    fallback = point
                if point.recall < TARGET_RECALL:
                    continue
                if best is None or (point.false_positive_rate, -point.recall, -t) < (
                    best.false_positive_rate,
                    -best.recall,
                    -best.vector_threshold,
                ):
                    best = point
    assert fallback is not None
    return (best, True) if best is not None else (fallback, False)


def _config(point: OperatingPoint) -> ScreeningConfig:
    return ScreeningConfig(
        vector_threshold=point.vector_threshold,
        use_phonetic=point.use_phonetic,
        rerank_threshold=point.rerank_threshold,
    )


def summarise(
    choice: EmbeddingChoice,
    results: list[QueryResult],
    ms_per_screen: float,
    *,
    reranker: str | None,
    rerank_thresholds: Sequence[float | None],
    fixed: OperatingPoint | None = None,
) -> ModelResult:
    if fixed is None:
        point, met = choose(results, rerank_thresholds)
    else:
        point, met = fixed, True
    config = _config(point)
    positives = {"positive"}
    negatives = {"negative_synthetic", "negative_hard"}

    # Re-score the operating point with decide_hits() itself. If the sweep and the production
    # code ever disagree, the report would describe a system that does not exist.
    recall = _rate(results, config, positives)
    fpr = _rate(results, config, negatives)
    if fixed is None and (
        abs(recall - point.recall) > 1e-9 or abs(fpr - point.false_positive_rate) > 1e-9
    ):
        raise AssertionError(f"sweep {point} disagrees with decide_hits(): {recall}, {fpr}")
    point = OperatingPoint(
        point.vector_threshold, point.use_phonetic, point.rerank_threshold, recall, fpr
    )
    met = met and recall >= TARGET_RECALL

    transforms = sorted(
        {t for r in results for t in r.row["transforms"] if r.row["kind"] == "positive"}
    )
    recall_by_transform = {}
    for t in transforms:
        subset = [r for r in results if r.row["kind"] == "positive" and t in r.row["transforms"]]
        recall_by_transform[t] = _rate(subset, config, positives)

    curve_recall, curve_fpr = _sweep(
        results, use_phonetic=point.use_phonetic, rerank_threshold=point.rerank_threshold
    )
    curve = [
        {
            "threshold": float(t),
            "recall": float(curve_recall[i]),
            "false_positive_rate": float(curve_fpr[i]),
        }
        for i, t in enumerate(THRESHOLDS)
        if round(float(t) * 100) % 5 == 0
    ]
    return ModelResult(
        embedding=choice.label,
        reranker=reranker if point.rerank_threshold is not None else None,
        chosen=point,
        target_met=met,
        recall_by_transform=recall_by_transform,
        fpr_by_kind={k: _rate(results, config, {k}) for k in sorted(negatives)},
        curve=curve,
        ms_per_screen=ms_per_screen,
        consistency_checked=fixed is None,
    )


def evaluate(
    choice: EmbeddingChoice,
    *,
    with_reranker: bool,
    fixed: OperatingPoint | None = None,
    queries: list[dict[str, Any]] | None = None,
) -> ModelResult:
    queries = queries if queries is not None else load_queries()
    entries, list_hash = list_entries()
    client = build_client(choice)
    collection = eval_collection(choice, client, entries, list_hash)
    results, ms = gather_all(client, collection, queries)

    reranker_name: str | None = None
    thresholds: list[float | None] = [None]
    if with_reranker:
        reranker: Reranker = CrossEncoderReranker(
            settings.reranker_model, cache_dir=settings.model_cache_dir
        )
        reranker_name = settings.reranker_model
        started = time.perf_counter()
        results = [
            QueryResult(r.row, rerank_candidates(reranker, r.row["query"], r.candidates))
            for r in results
        ]
        ms += (time.perf_counter() - started) * 1000 / max(len(results), 1)
        scores = np.array(
            [c.rerank_score for r in results for c in r.candidates if c.rerank_score is not None]
        )
        if scores.size:
            thresholds += sorted(
                {round(float(q), 3) for q in np.quantile(scores, np.linspace(0, 0.95, 20))}
            )
    result = summarise(
        choice, results, ms, reranker=reranker_name, rerank_thresholds=thresholds, fixed=fixed
    )
    # The best operating point with phonetic matching forced on and forced off, so the effect of
    # D10's phonetic leg is visible whichever way the overall choice falls.
    by_phonetic = {}
    if fixed is None:
        for use_phonetic in (True, False):
            point, met = choose(results, thresholds, phonetic_options=(use_phonetic,))
            by_phonetic["on" if use_phonetic else "off"] = {**asdict(point), "target_met": met}
    result.extra = {
        "collection": collection,
        "list_content_hash": list_hash,
        "queries": len(queries),
        "best_by_phonetic": by_phonetic,
    }
    return result


def standard_error(rate: float, n: int) -> float:
    return float(np.sqrt(rate * (1 - rate) / n)) if n else 0.0


def select_shipped(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Which compared configuration ships.

    1. Among configurations without a reranker that meet TARGET_RECALL, the lowest
       false-positive rate wins.
    2. The same embedding with a reranker replaces it only if it lowers the false-positive rate
       by more than two standard errors of the winner's rate over the negative set. A smaller
       lift is not distinguishable from the sampling noise of the dataset, and a reranker
       costs a second model and roughly double the latency per screen.

    Fixed before the Phase 13a comparison was read, apart from rule 2, which was added when
    the first comparison showed a lift of about one standard error (PLAN.md, Phase 13a).
    """
    met = [r for r in results if r["target_met"]]
    if not met:
        return max(results, key=lambda r: r["chosen"]["recall"])
    plain = [r for r in met if r["reranker"] is None]
    if not plain:
        return min(met, key=lambda r: r["chosen"]["false_positive_rate"])
    best = min(plain, key=lambda r: r["chosen"]["false_positive_rate"])
    negatives = sum(1 for q in load_queries() if q["kind"] != "positive")
    margin = 2 * standard_error(best["chosen"]["false_positive_rate"], negatives)
    for r in met:
        if r["reranker"] and r["embedding"] == best["embedding"]:
            lift = best["chosen"]["false_positive_rate"] - r["chosen"]["false_positive_rate"]
            if lift > margin:
                return r
    return best


def shipped_point() -> OperatingPoint:
    config = ScreeningConfig.from_settings()
    return OperatingPoint(
        config.vector_threshold, config.use_phonetic, config.rerank_threshold, 0.0, 0.0
    )


def dataset_info() -> dict[str, Any]:
    return {
        "name": GENERATOR_VERSION,
        "sha256": DATASET_SHA256,
        "list": LIST_DIR.name,
        "target_recall": TARGET_RECALL,
        "noise_model": "ours: transliteration, typo, dropped token, surname-first, particle",
    }


def as_dict(result: ModelResult) -> dict[str, Any]:
    return asdict(result)
