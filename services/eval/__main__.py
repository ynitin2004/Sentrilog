"""Command line for the evaluation harness. See services/eval/__init__.py.

    uv run python -m services.eval screening [--compare]
    uv run python -m services.eval face [--max-pairs N]
    uv run python -m services.eval extraction [--samples N] [--no-ocr] [--vlm]
    uv run python -m services.eval risk
    uv run python -m services.eval all
    uv run python -m services.eval build-screening-dataset

Common flags: --record writes an eval_runs row per suite; --write-results updates the committed
results in services/eval/results/; --report PATH appends Markdown (CI passes
$GITHUB_STEP_SUMMARY). Exits 1 if a blocking gate fails (see GATES in report.py).
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

from .datasets import RESULTS_DIR
from .report import gate_failures, render
from .store import record_run


def _write(suite: str, payload: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    (RESULTS_DIR / f"{suite}.json").write_bytes(data.encode("utf-8"))


def _load(suite: str) -> dict[str, Any] | None:
    path = RESULTS_DIR / f"{suite}.json"
    if not path.exists():
        return None
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def run_screening(compare: bool) -> dict[str, Any]:
    from . import screening

    if compare:
        results = []
        queries = screening.load_queries()
        for choice in screening.CANDIDATES:
            for with_reranker in (False, True):
                results.append(
                    screening.as_dict(
                        screening.evaluate(choice, with_reranker=with_reranker, queries=queries)
                    )
                )
        best = screening.select_shipped(results)
        return {"dataset": screening.dataset_info(), "compared": results, "chosen": best}

    shipped = screening.evaluate(
        screening.shipped_choice(),
        with_reranker=screening.shipped_point().rerank_threshold is not None,
        fixed=screening.shipped_point(),
    )
    return {"dataset": screening.dataset_info(), "shipped": screening.as_dict(shipped)}


def run_face(max_pairs: int | None) -> dict[str, Any]:
    from . import face

    return face.as_dict(face.evaluate(max_pairs=max_pairs))


def run_extraction(samples: int, ocr: bool, vlm: bool) -> dict[str, Any]:
    from . import extraction

    return extraction.as_dict(extraction.evaluate(samples=samples, with_ocr=ocr, with_vlm=vlm))


def run_risk(
    face_result: dict[str, Any] | None,
    screening_result: dict[str, Any] | None,
    policy_path: str | None = None,
) -> dict[str, Any]:
    from . import risk

    face_result = face_result or _load("face")
    screening_result = screening_result or _load("screening")
    if face_result is None or screening_result is None:
        raise SystemExit("risk needs face and screening results: run them first, or `all`")
    point = (screening_result.get("shipped") or screening_result["chosen"])["chosen"]
    decide: risk.Decider = risk.production_decider
    label = "built-in, version 0 (services/common/policy.py)"
    if policy_path:
        from pathlib import Path

        from services.common.policy import RiskPolicy

        policy = RiskPolicy.model_validate_json(Path(policy_path).read_text(encoding="utf-8"))
        decide, label = risk.policy_decider(policy), policy_path
    return risk.as_dict(
        risk.evaluate(
            genuine_scores=face_result["genuine_scores"],
            impostor_scores=face_result["impostor_scores_official"],
            screening_recall=point["recall"],
            screening_fpr=point["false_positive_rate"],
            decide=decide,
            policy=label,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m services.eval")
    parser.add_argument(
        "suite",
        choices=["screening", "face", "extraction", "risk", "all", "build-screening-dataset"],
    )
    parser.add_argument("--compare", action="store_true", help="screening: every candidate")
    parser.add_argument("--max-pairs", type=int, default=None, help="face: first N of each kind")
    parser.add_argument("--samples", type=int, default=300, help="extraction: identities")
    parser.add_argument("--no-ocr", action="store_true", help="extraction: skip the OCR path")
    parser.add_argument("--vlm", action="store_true", help="extraction: also call Gemini")
    parser.add_argument("--policy", default=None, help="risk: a policy JSON file to measure")
    parser.add_argument("--record", action="store_true", help="write eval_runs rows")
    parser.add_argument("--write-results", action="store_true", help="update results/*.json")
    parser.add_argument("--report", default=None, help="append Markdown to this file")
    args = parser.parse_args(argv)

    if args.suite == "build-screening-dataset":
        from .screening_data import build_dataset

        print(build_dataset())
        return 0

    outputs: dict[str, dict[str, Any]] = {}
    suites = ["screening", "face", "extraction", "risk"] if args.suite == "all" else [args.suite]
    for suite in suites:
        if suite == "screening":
            outputs[suite] = run_screening(args.compare)
        elif suite == "face":
            outputs[suite] = run_face(args.max_pairs)
        elif suite == "extraction":
            outputs[suite] = run_extraction(args.samples, not args.no_ocr, args.vlm)
        else:
            outputs[suite] = run_risk(outputs.get("face"), outputs.get("screening"), args.policy)

    # Persist before printing anything: a run can take twenty minutes, and the first one died
    # after finishing, on a console that could not encode the report (Windows cp1252, "≥").
    for suite, payload in outputs.items():
        if args.write_results:
            _write(suite, payload)
        if args.record:
            asyncio.run(record_run(suite, payload))
    markdown = render(outputs)
    if args.report:
        with open(args.report, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")
    print(markdown)

    failures = gate_failures(outputs)
    for failure in failures:
        print(f"GATE FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
