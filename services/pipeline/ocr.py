"""EasyOCR-based reader for the Machine-Readable Zone specifically -- not general-purpose OCR.
Closes the gap flagged in Phase 4: mrz.parse_td3() takes already-extracted MRZ line strings;
this is what actually produces them from a real image.

Deliberately approximate, and that's fine: any OCR misread gets caught downstream by
parse_td3()'s checksum validation, which is the actual safety net here, not this module's
line-detection heuristics. A misread MRZ fails its checksums and correctly falls through to
the VLM path (see extract.py) rather than being trusted.
"""

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from .extraction.mrz import fit_filler

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    import easyocr

# MRZ uses a fixed, constrained character set -- restricting recognition to it (rather than
# general alphanumeric + punctuation) meaningfully improves accuracy on this specific text.
_MRZ_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_MRZ_LINE_LENGTH = 44
_MIN_PLAUSIBLE_LENGTH = 30  # tolerate some OCR undershoot before discarding a candidate line

OCRResult = tuple[list[list[float]], str, float]


@lru_cache(maxsize=1)
def _get_reader() -> "easyocr.Reader":
    # Built once and cached: loading the model is slow (seconds) and memory-heavy (torch),
    # not something to redo on every call.
    #
    # `import easyocr` lives in here rather than at module scope because it pulls in torch,
    # which costs seconds of import time and hundreds of MB of RSS. Every module that imports
    # `activities.py` -- the whole workflow test suite, scripts/chaos_test.py -- used to pay
    # that just to reach a function it never calls, and in an environment where torch's native
    # DLLs won't load at all (an endpoint-security Application Control policy blocking
    # torch_python.dll, which is how this surfaced) the import error took out five unrelated
    # test modules at collection time. Deferring it to first real OCR call means only code that
    # actually reads an MRZ depends on torch being loadable.
    import easyocr

    return easyocr.Reader(["en"], gpu=False, verbose=False)


def _rows(ocr_results: list[OCRResult]) -> list[tuple[float, str]]:
    """Groups detected boxes into text rows: (row top, the row's text read left to right).

    EasyOCR does not return one box per MRZ line. A long run of '<' filler splits a line into
    separate boxes ("P<INDRAHMAN<<ELIF<<<<<<<" and "<<<<<<" on the same baseline), and neither
    piece alone is long enough to look like an MRZ line. Found by the Phase 13 evaluation
    harness: before rows were merged, 30 of 30 synthetic data pages yielded no MRZ at all.

    Two boxes are on the same row when their vertical centres are closer than half the taller
    box's height -- tight enough that the two MRZ lines, which sit one line-height apart, never
    merge into one.
    """
    boxes = []
    for bbox, text, _confidence in ocr_results:
        ys = [point[1] for point in bbox]
        xs = [point[0] for point in bbox]
        boxes.append((min(ys), max(ys), min(xs), re.sub(r"\s", "", text).upper()))
    boxes.sort(key=lambda b: (b[0] + b[1]) / 2)

    rows: list[list[tuple[float, float, float, str]]] = []
    for box in boxes:
        centre, height = (box[0] + box[1]) / 2, box[1] - box[0]
        if rows:
            last = rows[-1][-1]
            last_centre, last_height = (last[0] + last[1]) / 2, last[1] - last[0]
            if abs(centre - last_centre) < max(height, last_height) / 2:
                rows[-1].append(box)
                continue
        rows.append([box])
    return [
        (min(b[0] for b in row), "".join(b[3] for b in sorted(row, key=lambda b: b[2])))
        for row in rows
    ]


def _assemble_mrz_lines(ocr_results: list[OCRResult]) -> tuple[str, str] | None:
    """Pure assembly logic, independently testable without EasyOCR/torch: given detected text
    boxes, merges them into rows (see _rows) and picks the bottom two MRZ-length-ish rows (the
    MRZ is always at the bottom of the document), ordered top-to-bottom by position, not by
    whatever order the OCR engine happened to return them in.
    """
    candidates = [
        (top_y, text) for top_y, text in _rows(ocr_results) if len(text) >= _MIN_PLAUSIBLE_LENGTH
    ]
    if len(candidates) < 2:
        return None

    candidates.sort(key=lambda c: c[0])
    line1, line2 = candidates[-2][1], candidates[-1][1]
    # Widen the filler run rather than pad at the end: OCR shortens long '<' runs, and end
    # padding strands everything after the run out of position (see mrz.fit_filler).
    return fit_filler(line1), fit_filler(line2)


def read_mrz_lines(image_bytes: bytes) -> tuple[str, str] | None:
    """Returns the two candidate MRZ lines, or None if nothing MRZ-shaped was found. Callers
    must still run the result through mrz.parse_td3() and check .valid -- this function makes
    no claim about correctness, only about having found *something* worth checksum-validating.
    """
    reader: Any = _get_reader()
    results: list[OCRResult] = reader.readtext(
        image_bytes, allowlist=_MRZ_ALLOWLIST, detail=1, paragraph=False
    )
    return _assemble_mrz_lines(results)
