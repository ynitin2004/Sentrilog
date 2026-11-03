"""ICAO 9303 TD3 (passport) Machine-Readable Zone parsing.

Deterministic, checksum-validated, and free -- no model call involved. This is preferred over
the VLM path whenever a document has an MRZ, per PLAN.md Phase 4: the checksums make it more
trustworthy than a model's best guess, not just cheaper.

Reference: ICAO Doc 9303 Part 4. The two-line TD3 zone is 44 characters per line; each of the
document number, date of birth, and expiry date carries its own check digit, plus one overall
composite check digit over all of them together.
"""

import datetime as dt
import itertools
import math
import re
from dataclasses import dataclass, field

from .country_codes import TD3_STATE_CODES

_WEIGHTS = (7, 3, 1)
_LINE1_SHAPE = re.compile(r"^P[A-Z<][A-Z<]{3}[A-Z<]{39}$")


def _char_value(c: str) -> int:
    if c == "<":
        return 0
    if c.isdigit():
        return int(c)
    if c.isalpha():
        return ord(c.upper()) - ord("A") + 10
    raise ValueError(f"invalid MRZ character: {c!r}")


def _check_digit(s: str) -> int:
    total = sum(_char_value(c) * _WEIGHTS[i % 3] for i, c in enumerate(s))
    return total % 10


def _parse_yymmdd(raw: str, *, assume_future_century: bool) -> dt.date | None:
    if not raw.isdigit() or len(raw) != 6:
        return None
    yy, mm, dd = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    current_yy = dt.date.today().year % 100
    if assume_future_century:
        # Expiry dates on modern MRZ documents are always 2000s -- there is no ambiguity to
        # resolve the way there is for date of birth.
        century = 2000
    else:
        # A birth date can never be in the future: if YY is later than the current two-digit
        # year, it must be the previous century.
        century = 1900 if yy > current_yy else 2000
    try:
        return dt.date(century + yy, mm, dd)
    except ValueError:
        return None


@dataclass
class MRZResult:
    valid: bool
    full_name: str | None = None
    document_number: str | None = None
    nationality: str | None = None
    date_of_birth: dt.date | None = None
    expiry_date: dt.date | None = None
    document_type: str | None = None
    failed_checks: list[str] = field(default_factory=list)


def parse_td3(line1: str, line2: str) -> MRZResult:
    line1, line2 = line1.strip().upper(), line2.strip().upper()
    if len(line1) != 44 or len(line2) != 44:
        return MRZResult(valid=False, failed_checks=["line_length"])

    failed: list[str] = []

    document_number = line2[0:9].rstrip("<")
    if _check_digit(line2[0:9]) != _char_value(line2[9]):
        failed.append("document_number")

    nationality = line2[10:13]

    dob_raw = line2[13:19]
    if _check_digit(dob_raw) != _char_value(line2[19]):
        failed.append("date_of_birth")

    expiry_raw = line2[21:27]
    if _check_digit(expiry_raw) != _char_value(line2[27]):
        failed.append("expiry_date")

    # Personal number is frequently unused; ICAO 9303 permits '<' in its check-digit position
    # in that case, so a literal '<' there is treated as valid rather than a checksum failure.
    personal_number_field = line2[28:42]
    personal_check_char = line2[42]
    if personal_check_char != "<" and _check_digit(personal_number_field) != _char_value(
        personal_check_char
    ):
        failed.append("personal_number")

    composite = line2[0:10] + line2[13:20] + line2[21:28] + line2[28:43]
    if _check_digit(composite) != _char_value(line2[43]):
        failed.append("composite")

    date_of_birth = _parse_yymmdd(dob_raw, assume_future_century=False)
    expiry_date = _parse_yymmdd(expiry_raw, assume_future_century=True)
    if date_of_birth is None:
        failed.append("date_of_birth_format")
    if expiry_date is None:
        failed.append("expiry_date_format")

    # Line 1 has no check digits at all, so its shape is the only defence against OCR handing
    # over some other row of the page (the harness caught a printed header being parsed as the
    # name). A TD3 passport line starts "P", names a real issuing state, and separates
    # surname from given names with "<<".
    name_field = line1[5:44]
    if not _LINE1_SHAPE.match(line1) or "<<" not in name_field or line1[2:5] not in TD3_STATE_CODES:
        failed.append("line1_format")
    parts = name_field.split("<<", 1)
    surname = parts[0].replace("<", " ").strip()
    # The given names end at the next "<<": ICAO 9303 allows only filler after them, so anything
    # beyond is OCR noise in the filler, not part of the name.
    given_names = parts[1].split("<<", 1)[0].replace("<", " ").strip() if len(parts) > 1 else ""
    full_name = f"{given_names} {surname}".strip() if given_names else surname

    return MRZResult(
        valid=not failed,
        full_name=full_name or None,
        document_number=document_number or None,
        nationality=nationality.rstrip("<") or None,
        date_of_birth=date_of_birth,
        expiry_date=expiry_date,
        document_type=line1[0:2].rstrip("<") or None,
        failed_checks=failed,
    )


# --- Tolerant reading (Phase 13) ------------------------------------------------------------
#
# OCR confuses glyph pairs the MRZ font makes alike (0/O, 5/S, 8/B, 7/Z, 1/I), and the
# evaluation harness (services/eval/extraction.py) found those confusions defeating almost every
# otherwise-correct read of a rendered data page. Every TD3 position is defined as digits only,
# letters only, or either, so:
#
# - a letter read in a digits-only position (or the reverse) has only one or two plausible
#   meanings, and is replaced by them;
# - the document number, where letters and digits are both legal, is never guessed at. The
#   harness showed why: its characters carry the same weights in the composite check digit as
#   in their own, so any substitution that keeps the document-number check passing keeps the
#   composite passing too. Searching that field produced confident wrong numbers (8 of 14
#   accepted reads in one run), so a failed document-number check goes to the VLM fallback.
#
# and each check-digit field is resolved on its own. A field that already passes its check digit
# is kept exactly as read. A field that fails takes the reading needing the fewest substitutions
# that passes -- and only if that reading is the only one at that distance. If two readings tie,
# the document is not trusted and goes to the VLM fallback: a repair is never allowed to choose
# between two valid documents. The composite check digit then has to pass over the result.

_DIGIT_READINGS = {"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "T": "7",
                   "Z": "27", "S": "5", "B": "8", "G": "6"}  # fmt: skip
_LETTER_READINGS = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "7": "Z", "8": "B"}
_TO_LETTER = str.maketrans(_LETTER_READINGS)
_LINE2_NUMERIC = {9, *range(13, 20), *range(21, 28), 43}
_LINE2_ALPHA = set(range(10, 13))
# (field positions, its check-digit position) on line 2
_LINE2_FIELDS = ((range(0, 9), 9), (range(13, 19), 19), (range(21, 27), 27))
# Readings are only ever tried in digits-only positions, so the document number (0-8) keeps
# its characters as read and only its check digit (9) can be repaired.
_MAX_READINGS = 4096


def fit_filler(line: str) -> str:
    """OCR compresses long runs of '<' filler, which shifts every character after the run out of
    its fixed position. A short line has its longest filler run widened back to 44 characters,
    rather than being padded at the end, which would strand a final check digit mid-line. A
    long line is truncated."""
    if len(line) >= 44:
        return line[:44]
    runs = _filler_runs(line)
    if not runs:
        return line.ljust(44, "<")
    start, end = max(runs, key=lambda r: r[1] - r[0])
    return line[:start] + "<" * (end - start + 44 - len(line)) + line[end:]


def _filler_runs(line: str) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(line):
        if line[i] == "<":
            j = i
            while j < len(line) and line[j] == "<":
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _readings(i: int, c: str) -> str:
    if i in _LINE2_NUMERIC:
        return c if c.isdigit() else _DIGIT_READINGS.get(c, c)
    if i in _LINE2_ALPHA:
        return _LETTER_READINGS.get(c, c)
    return c


def _resolve_field(line2: str, positions: range, check: int) -> str | None:
    """The field (with its check digit) as read if it passes; otherwise the unique
    fewest-substitutions reading that does; None if there is none or a tie."""
    span = list(positions) + [check]
    original = "".join(line2[i] for i in span)
    if _check_digit(original[:-1]) == _char_value_or(original[-1]):
        return original
    options = [_readings(i, line2[i]) for i in span]
    if math.prod(len(o) for o in options) > _MAX_READINGS:
        return None
    best: dict[int, set[str]] = {}
    for reading in itertools.product(*options):
        candidate = "".join(reading)
        if not candidate[-1].isdigit():
            continue
        if _check_digit(candidate[:-1]) == int(candidate[-1]):
            changes = sum(a != b for a, b in zip(candidate, original, strict=True))
            best.setdefault(changes, set()).add(candidate)
    if not best:
        return None
    winners = best[min(best)]
    return next(iter(winners)) if len(winners) == 1 else None


def _char_value_or(c: str) -> int:
    try:
        return _char_value(c)
    except ValueError:
        return -1


def parse_td3_tolerant(line1: str, line2: str) -> MRZResult:
    line1 = fit_filler(line1.strip().upper())
    # Positions 2-4 (issuing state) and the name are letters or '<' only.
    line1 = line1[:2] + line1[2:].translate(_TO_LETTER)
    line2 = fit_filler(line2.strip().upper())

    exact = parse_td3(line1, line2)
    if exact.valid:
        return exact

    chars = [_readings(i, c)[0] if i in _LINE2_ALPHA else c for i, c in enumerate(line2)]
    for positions, check in _LINE2_FIELDS:
        resolved = _resolve_field(line2, positions, check)
        if resolved is None:
            return MRZResult(valid=False, failed_checks=[*exact.failed_checks, "unresolved"])
        for i, c in zip(list(positions) + [check], resolved, strict=True):
            chars[i] = c
    composite = _readings(43, line2[43])
    candidates = [parse_td3(line1, "".join(chars[:43]) + d) for d in composite]
    passing = [r for r in candidates if r.valid]
    if len(passing) == 1:
        return passing[0]
    return MRZResult(valid=False, failed_checks=[*exact.failed_checks, "unresolved"])
