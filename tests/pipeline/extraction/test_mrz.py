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
