import datetime as dt

from services.pipeline.extraction.mrz import _char_value, _check_digit, parse_td3


def test_check_digit_hand_verifiable_cases() -> None:
    # Verified by hand, independent of any MRZ context: value('1')=1, weight[0]=7 -> 7.
    assert _check_digit("1") == 7
    # A=10, weight[0]=7 -> 70; B=11, weight[1]=3 -> 33; total 103 -> 3.
    assert _check_digit("AB") == 3
    # All fillers are worth 0 regardless of weight.
    assert _check_digit("<<<") == 0


def test_char_value_rejects_invalid_characters() -> None:
    import pytest

    with pytest.raises(ValueError):
        _char_value("!")


def _build_td3(
    *,
    country: str = "USA",
    surname: str = "SMITH",
    given_names: str = "JOHN ROBERT",
    document_number: str = "123456789",
    nationality: str = "USA",
    dob: str = "800101",
    sex: str = "M",
    expiry: str = "300101",
) -> tuple[str, str]:
    """Builds a self-consistent TD3 MRZ using the module's own checksum function -- this
    validates field-extraction logic independently (pure string slicing, no checksums
    involved) and checksum *sensitivity* (corrupting any field must flip valid to False).
    Checksum *correctness* itself is verified separately and by hand in the test above.
    """
    name_field = f"{surname}<<{given_names.replace(' ', '<')}".ljust(39, "<")
    line1 = f"P<{country}{name_field}"

    doc_num = document_number.ljust(9, "<")
    doc_num_check = str(_check_digit(doc_num))
    dob_check = str(_check_digit(dob))
    expiry_check = str(_check_digit(expiry))
    personal_number = "<" * 14
    personal_check = "<"

    composite = (
        doc_num
        + doc_num_check
        + dob
        + dob_check
        + expiry
        + expiry_check
        + personal_number
        + personal_check
    )
    composite_check = str(_check_digit(composite))

    line2 = (
        doc_num
        + doc_num_check
        + nationality
        + dob
        + dob_check
        + sex
        + expiry
        + expiry_check
        + personal_number
        + personal_check
        + composite_check
    )
    assert len(line1) == 44
    assert len(line2) == 44
    return line1, line2


def test_valid_mrz_parses_all_fields_correctly() -> None:
    line1, line2 = _build_td3()
    result = parse_td3(line1, line2)

    assert result.valid is True
    assert result.failed_checks == []
    assert result.full_name == "JOHN ROBERT SMITH"
    assert result.document_number == "123456789"
    assert result.nationality == "USA"
    assert result.date_of_birth == dt.date(1980, 1, 1)
    assert result.expiry_date == dt.date(2030, 1, 1)
    assert result.document_type == "P"


def test_corrupted_document_number_fails_checksum_not_crash() -> None:
    line1, line2 = _build_td3()
    # Flip one digit in the document number without updating its check digit.
    corrupted = "9" if line2[0] != "9" else "8"
    line2 = corrupted + line2[1:]

    result = parse_td3(line1, line2)

    assert result.valid is False
    assert "document_number" in result.failed_checks
    assert "composite" in result.failed_checks  # the overall composite covers this field too


def test_wrong_length_lines_are_rejected_not_indexed_out_of_range() -> None:
    result = parse_td3("TOO SHORT", "ALSO TOO SHORT")
    assert result.valid is False
    assert result.failed_checks == ["line_length"]


def test_unused_personal_number_with_literal_filler_check_is_valid() -> None:
    # ICAO 9303 explicitly permits '<' in the personal-number check-digit position when the
    # field is unused, rather than requiring a computed 0 -- this is the common real-world case.
    line1, line2 = _build_td3()
    result = parse_td3(line1, line2)
    assert "personal_number" not in result.failed_checks


def test_tolerant_reading_fixes_digit_letter_confusions_by_position() -> None:
    from services.pipeline.extraction.mrz import parse_td3_tolerant

    line1, line2 = _build_td3(surname="SOSA", given_names="BOB")
    # OCR-style misreads: letters in the name read as digits, the nationality with a digit in
    # it, and digits in the date of birth read as letters.
    bad1 = line1.replace("SOSA", "505A").replace("BOB", "8O8")
    bad2 = line2[:10] + "U5A" + line2[13:].replace("800101", "8OO1O1", 1)
    assert parse_td3(bad1, bad2).valid is False
    result = parse_td3_tolerant(bad1, bad2)
    assert result.valid, result.failed_checks
    assert (result.full_name, result.nationality) == ("BOB SOSA", "USA")
    assert result.date_of_birth == parse_td3(line1, line2).date_of_birth


def test_tolerant_reading_never_guesses_inside_the_document_number() -> None:
    """The composite check digit weights the document number exactly as its own check does,
    so a compensating misread there passes both. A failed document-number check must fall
    through to the VLM rather than be searched for a passing reading."""
    from services.pipeline.extraction.mrz import parse_td3_tolerant

    line1, line2 = _build_td3(document_number="T54JTZR84")
    bad2 = "TS4JTZR84" + line2[9:]  # the 5 read as S, inside the mixed-character field
    assert parse_td3_tolerant(line1, bad2).valid is False


def test_noise_in_the_name_filler_is_not_part_of_the_name() -> None:
    line1, line2 = _build_td3(surname="OCONNOR", given_names="GIULIA HANS")
    noisy = line1[:30] + "S" + line1[31:]  # inside the trailing filler
    assert noisy != line1
    assert parse_td3(noisy, line2).full_name == "GIULIA HANS OCONNOR"


def test_a_row_that_is_not_a_td3_line_1_is_rejected() -> None:
    _, line2 = _build_td3()
    header = "PASSPORTSPECIMENSYNTHETICTESTDOCUMENT".ljust(44, "<")
    result = parse_td3(header, line2)
    assert result.valid is False
    assert "line1_format" in result.failed_checks


def test_tolerant_reading_restores_a_filler_run_ocr_compressed() -> None:
    from services.pipeline.extraction.mrz import parse_td3_tolerant

    line1, line2 = _build_td3()
    compressed = line2[:28] + "<" * 5 + line2[42:]  # 14-char filler read as 5
    assert parse_td3(line1, compressed.ljust(44, "<")).valid is False
    assert parse_td3_tolerant(line1, compressed).valid


def test_tolerant_reading_cannot_make_a_wrong_digit_pass() -> None:
    from services.pipeline.extraction.mrz import parse_td3_tolerant

    line1, line2 = _build_td3()
    wrong = line2[:13] + "9" + line2[14:]  # a real digit misread as another real digit
    assert parse_td3_tolerant(line1, wrong).valid is False
