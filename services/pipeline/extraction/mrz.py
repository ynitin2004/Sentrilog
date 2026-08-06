"""ICAO 9303 TD3 (passport) Machine-Readable Zone parsing.

Deterministic, checksum-validated, and free -- no model call involved. This is preferred over
the VLM path whenever a document has an MRZ, per PLAN.md Phase 4: the checksums make it more
trustworthy than a model's best guess, not just cheaper.

Reference: ICAO Doc 9303 Part 4. The two-line TD3 zone is 44 characters per line; each of the
document number, date of birth, and expiry date carries its own check digit, plus one overall
composite check digit over all of them together.
"""

import datetime as dt
from dataclasses import dataclass, field

_WEIGHTS = (7, 3, 1)


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

    name_field = line1[5:44]
    parts = name_field.split("<<", 1)
    surname = parts[0].replace("<", " ").strip()
    given_names = parts[1].replace("<", " ").strip() if len(parts) > 1 else ""
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
