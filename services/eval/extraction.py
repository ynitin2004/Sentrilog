"""Extraction evaluation: field-level precision, recall and F1 on synthetic passport data pages
with known ground truth (D28).

Three paths, reported separately because they fail differently:

- mrz-parse: generated MRZ lines with OCR-style character confusions injected (O/0, I/1, S/5,
  B/8, '<'/K) at a fixed rate, through the production extract_from_mrz(). No OCR, so it runs
  everywhere. The number that matters most is wrong_but_accepted -- a misread that still passes
  every ICAO check digit -- because the MRZ path auto-trusts what it accepts (confidence 0.99).
- mrz-ocr: rendered images through the production EasyOCR reader, then the same parser. Needs
  torch, which Application Control blocks on the dev host, so it runs in CI (Linux) and is
  reported as skipped where it cannot run, never as zero.
- vlm: rendered images through Gemini. Off unless asked for (--vlm) and a key is set; synthetic
  images only (P-1).

An image whose MRZ fails its checksums falls through to the VLM in production. Here that is
counted as "routed to fallback" -- coverage, not an error -- and the fields it would have
produced are not invented.

    uv run python -m services.eval extraction [--samples N] [--vlm]
"""

import datetime as dt
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from services.pipeline.extraction.extract import extract_from_mrz
from services.pipeline.extraction.schemas import IDDocument

from .datasets import sha256_bytes
from .metrics import FieldCounts, field_counts
from .synthetic_id import SyntheticIdentity, build_td3, find_font, random_identity, render_data_page

GENERATOR_VERSION = "synthetic-td3-v1"
SEED = 13
CONFUSIONS = {
    "O": "0",
    "0": "O",
    "I": "1",
    "1": "I",
    "S": "5",
    "5": "S",
    "B": "8",
    "8": "B",
    "<": "K",
}
CORRUPTION_RATE = 0.02
FIELDS = ("full_name", "document_number", "date_of_birth", "expiry_date", "nationality")


@dataclass
class PathResult:
    path: str
    status: str  # "ran" | "skipped: <reason>"
    samples: int = 0
    accepted: int = 0
    routed_to_fallback: int = 0
    wrong_but_accepted: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    per_field_f1: dict[str, float] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)


def identities(n: int) -> list[SyntheticIdentity]:
    rng = random.Random(SEED)
    return [random_identity(rng) for _ in range(n)]


def dataset_sha256(ids: list[SyntheticIdentity]) -> str:
    """The generated ground truth is the dataset; its hash identifies it as exactly as a file's."""
    rows = "\n".join(f"{i.full_name}|{i.document_number}|{i.nationality}|{i.date_of_birth}|"
                     f"{i.expiry_date}|{i.sex}" for i in ids)  # fmt: skip
    return sha256_bytes(f"{GENERATOR_VERSION}\n{rows}".encode())


def _truth(identity: SyntheticIdentity) -> dict[str, str | None]:
    return {
        "full_name": identity.full_name,
        "document_number": identity.document_number,
        "date_of_birth": identity.date_of_birth.isoformat(),
        "expiry_date": identity.expiry_date.isoformat(),
        "nationality": identity.nationality,
    }


def _actual(document: IDDocument | None) -> dict[str, str | None]:
    if document is None:
        return dict.fromkeys(FIELDS)
    return {
        "full_name": document.full_name,
        "document_number": document.document_number,
        "date_of_birth": document.date_of_birth.isoformat(),
        "expiry_date": document.expiry_date.isoformat() if document.expiry_date else None,
        "nationality": document.nationality,
    }


def _score(
    path: str, ids: list[SyntheticIdentity], run: Callable[[SyntheticIdentity], IDDocument | None]
) -> PathResult:
    totals = {f: FieldCounts() for f in FIELDS}
    result = PathResult(path=path, status="ran", samples=len(ids))
    for identity in ids:
        document = run(identity)
        if document is None:
            result.routed_to_fallback += 1
            continue
        result.accepted += 1
        truth, actual = _truth(identity), _actual(document)
        if truth != actual:
            result.wrong_but_accepted += 1
        for f in FIELDS:
            totals[f] = totals[f] + field_counts(truth[f], actual[f])
    overall = sum(totals.values(), FieldCounts())
    result.precision, result.recall, result.f1 = overall.precision, overall.recall, overall.f1
    result.per_field_f1 = {f: totals[f].f1 for f in FIELDS}
    return result


def _corrupt(line: str, rng: random.Random) -> str:
    return "".join(
        CONFUSIONS[c] if c in CONFUSIONS and rng.random() < CORRUPTION_RATE else c for c in line
    )


def evaluate_mrz_parse(ids: list[SyntheticIdentity]) -> PathResult:
    rng = random.Random(SEED + 1)

    def run(identity: SyntheticIdentity) -> IDDocument | None:
        line1, line2 = build_td3(identity)
        result = extract_from_mrz(_corrupt(line1, rng), _corrupt(line2, rng))
        return result.document if result is not None else None

    result = _score("mrz-parse", ids, run)
    result.notes = {"corruption_rate": CORRUPTION_RATE, "confusions": "".join(CONFUSIONS)}
    return result


def evaluate_mrz_ocr(ids: list[SyntheticIdentity]) -> PathResult:
    try:
        from services.pipeline import ocr

        ocr._get_reader()
    except (ImportError, OSError) as exc:
        # torch_python.dll blocked by Application Control surfaces as ImportError/OSError.
        return PathResult(
            path="mrz-ocr", status=f"skipped: OCR unavailable here ({type(exc).__name__})"
        )

    font = find_font()

    def run(identity: SyntheticIdentity) -> IDDocument | None:
        lines = ocr.read_mrz_lines(render_data_page(identity, font_path=font))
        if lines is None:
            return None
        result = extract_from_mrz(*lines)
        return result.document if result is not None else None

    result = _score("mrz-ocr", ids, run)
    result.notes = {"font": font or "PIL default"}
    return result


def evaluate_vlm(ids: list[SyntheticIdentity]) -> PathResult:
    from services.pipeline.config import settings
    from services.pipeline.extraction.extract import extract_with_vlm
    from services.pipeline.extraction.vlm import GeminiVLMClient

    key = settings.gemini_api_key.get_secret_value()
    if not key:
        return PathResult(path="vlm", status="skipped: no GEMINI_API_KEY")
    client = GeminiVLMClient(api_key=key, model=settings.gemini_model)
    font = find_font()
    confidences: list[float] = []

    def run(identity: SyntheticIdentity) -> IDDocument | None:
        result = extract_with_vlm(
            client, render_data_page(identity, font_path=font), "image/jpeg", max_retries=3
        )
        confidences.append(result.confidence)
        return result.document

    result = _score("vlm", ids, run)
    result.notes = {"model": settings.gemini_model, "confidences": confidences}
    return result


@dataclass
class ExtractionResult:
    dataset: str
    dataset_sha256: str
    paths: list[PathResult]
    generated_on: str = field(default_factory=lambda: dt.date.today().isoformat())


def evaluate(
    *,
    samples: int = 300,
    with_ocr: bool = True,
    ocr_samples: int = 100,
    with_vlm: bool = False,
    vlm_samples: int = 20,
) -> ExtractionResult:
    ids = identities(samples)
    paths = [evaluate_mrz_parse(ids)]
    if with_ocr:
        paths.append(evaluate_mrz_ocr(ids[:ocr_samples]))
    if with_vlm:
        paths.append(evaluate_vlm(ids[:vlm_samples]))
    return ExtractionResult(
        dataset=GENERATOR_VERSION, dataset_sha256=dataset_sha256(ids), paths=paths
    )


def as_dict(result: ExtractionResult) -> dict[str, Any]:
    return asdict(result)
