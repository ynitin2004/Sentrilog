"""Markdown report and blocking gates.

Most numbers are reported, not gated: a harness that fails CI on every metric gets its
thresholds loosened until it means nothing. GATES holds the few that block a merge, each with
its reason. Everything else lands in the job summary for a human to read.
"""

from typing import Any

# Blocking. Each floor is set from a measured run (docs/PHASE-LOG.md, Phase 13a), with room
# for run-to-run noise only where a suite has any.
GATES = {
    # Missing a listed person is the failure the product exists to prevent (STUDY.md, Phase 13).
    "screening.recall": 0.95,
    # Regression floor for the shipped face model: 95.0% TAR@FAR=1e-3 measured on LFW DevTest
    # (2026-11-03), less two points for onnxruntime builds that differ in the last digits of a
    # score -- enough to move a pair across the threshold, not enough to move the rate by two.
    "face.tar_at_far_1e-3": 0.93,
}


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _screening(payload: dict[str, Any]) -> list[str]:
    d = payload["dataset"]
    lines = [
        "### Screening",
        f"Dataset `{d['name']}` (sha256 `{d['sha256'][:12]}…`) against `{d['list']}`, "
        f"target recall {_pct(d['target_recall'])}. Noise model: {d['noise_model']}.",
        "",
        "| Embedding | Reranker | Threshold | Phonetic | Recall | FPR | FPR synthetic | "
        "FPR hard | ms/screen |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    rows = payload.get("compared") or [payload["shipped"]]
    for r in rows:
        c = r["chosen"]
        rerank = f"{r['reranker']} ≥ {c['rerank_threshold']}" if r["reranker"] else "—"
        lines.append(
            f"| {r['embedding']} | {rerank} | {c['vector_threshold']:.2f} | "
            f"{'on' if c['use_phonetic'] else 'off'} | {_pct(c['recall'])} | "
            f"{_pct(c['false_positive_rate'])} | {_pct(r['fpr_by_kind']['negative_synthetic'])} | "
            f"{_pct(r['fpr_by_kind']['negative_hard'])} | {r['ms_per_screen']:.1f} |"
        )
    focus = payload.get("chosen") or payload["shipped"]
    lines += [
        "",
        "Recall by transform: "
        + ", ".join(f"{k} {_pct(v)}" for k, v in focus["recall_by_transform"].items()),
    ]
    return lines


def _face(payload: dict[str, Any]) -> list[str]:
    tar = ", ".join(f"TAR@FAR={k}: {_pct(v)}" for k, v in payload["tar_at_far"].items())
    official = ", ".join(f"FAR={k}: {_pct(v)}" for k, v in payload["tar_at_far_official"].items())
    thresholds = ", ".join(f"{k}: {v:.3f}" for k, v in payload["threshold_at_far"].items())
    return [
        "### Face match",
        f"`{payload['dataset']}`: {payload['pairs_genuine']} genuine pairs, "
        f"{payload['cross_identity_impostor_pairs']:,} cross-identity impostor pairs, "
        f"{payload['no_face_images']} of {payload['images']} images with no face detected.",
        "",
        f"- {tar} (thresholds {thresholds})",
        f"- Official 500 mismatched pairs: {official}",
        f"- Genuine median {payload['genuine_percentiles'].get('p50', 0):.3f}, "
        f"impostor p99 {payload['impostor_percentiles'].get('p99', 0):.3f}",
        f"- Caveat: {payload['caveat']}",
    ]


def _extraction(payload: dict[str, Any]) -> list[str]:
    lines = [
        "### Extraction",
        f"`{payload['dataset']}` (sha256 `{payload['dataset_sha256'][:12]}…`).",
        "",
        "| Path | Status | Samples | Accepted | To fallback | Wrong but accepted | F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in payload["paths"]:
        lines.append(
            f"| {p['path']} | {p['status']} | {p['samples']} | {p['accepted']} | "
            f"{p['routed_to_fallback']} | {p['wrong_but_accepted']} | "
            f"{p['f1']:.4f} |"
        )
    return lines


def _risk(payload: dict[str, Any]) -> list[str]:
    return [
        "### Risk policy",
        f"Policy: {payload['policy']}. Priors: "
        + ", ".join(f"{k} {_pct(v)}" for k, v in payload["priors"].items())
        + ".",
        "",
        f"- Auto-clear precision {_pct(payload['auto_clear_precision'])}, "
        f"review burden {_pct(payload['review_burden'])}",
        f"- Leak rates: impostor {_pct(payload['leak_rate_impostor'])}, "
        f"listed person {_pct(payload['leak_rate_listed'])}",
        f"- Caveat: {payload['caveat']}",
    ]


def render(outputs: dict[str, dict[str, Any]]) -> str:
    lines = ["## Evaluation harness"]
    for suite, payload in outputs.items():
        lines.append("")
        lines += {"screening": _screening, "face": _face, "extraction": _extraction, "risk": _risk}[
            suite
        ](payload)
    return "\n".join(lines)


def gate_failures(outputs: dict[str, dict[str, Any]]) -> list[str]:
    failures = []
    screening = outputs.get("screening")
    if screening and "shipped" in screening:
        recall = screening["shipped"]["chosen"]["recall"]
        if recall < GATES["screening.recall"]:
            failures.append(f"screening recall {recall:.4f} < {GATES['screening.recall']}")
    face = outputs.get("face")
    if face:
        tar = face["tar_at_far"].get("0.001", 0.0)
        if tar < GATES["face.tar_at_far_1e-3"]:
            failures.append(f"face TAR@FAR=1e-3 {tar:.4f} < {GATES['face.tar_at_far_1e-3']}")
    return failures
