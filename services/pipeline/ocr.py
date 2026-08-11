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

import easyocr

# MRZ uses a fixed, constrained character set -- restricting recognition to it (rather than
# general alphanumeric + punctuation) meaningfully improves accuracy on this specific text.
_MRZ_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_MRZ_LINE_LENGTH = 44
_MIN_PLAUSIBLE_LENGTH = 30  # tolerate some OCR undershoot before discarding a candidate line

OCRResult = tuple[list[list[float]], str, float]


@lru_cache(maxsize=1)
def _get_reader() -> easyocr.Reader:
    # Built once and cached: loading the model is slow (seconds) and memory-heavy (torch),
    # not something to redo on every call.
    return easyocr.Reader(["en"], gpu=False, verbose=False)


def _assemble_mrz_lines(ocr_results: list[OCRResult]) -> tuple[str, str] | None:
    """Pure assembly logic, independently testable without EasyOCR/torch: given detected text
    boxes, picks the bottom two MRZ-length-ish lines (the MRZ is always at the bottom of the
    document) ordered top-to-bottom by their own bounding box, not by whatever order the OCR
    engine happened to return them in.
    """
    candidates: list[tuple[float, str]] = []
    for bbox, text, _confidence in ocr_results:
        cleaned = re.sub(r"\s", "", text).upper()
        if len(cleaned) >= _MIN_PLAUSIBLE_LENGTH:
            top_y = min(point[1] for point in bbox)
            candidates.append((top_y, cleaned))

    if len(candidates) < 2:
        return None

    candidates.sort(key=lambda c: c[0])
    line1, line2 = candidates[-2][1], candidates[-1][1]
    return (
        line1.ljust(_MRZ_LINE_LENGTH, "<")[:_MRZ_LINE_LENGTH],
        line2.ljust(_MRZ_LINE_LENGTH, "<")[:_MRZ_LINE_LENGTH],
    )


def read_mrz_lines(image_bytes: bytes) -> tuple[str, str] | None:
    """Returns the two candidate MRZ lines, or None if nothing MRZ-shaped was found. Callers
    must still run the result through mrz.parse_td3() and check .valid -- this function makes
    no claim about correctness, only about having found *something* worth checksum-validating.
    """
    reader = _get_reader()
    results: list[OCRResult] = reader.readtext(
        image_bytes, allowlist=_MRZ_ALLOWLIST, detail=1, paragraph=False
    )
    return _assemble_mrz_lines(results)
