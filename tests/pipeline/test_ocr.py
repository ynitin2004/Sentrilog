from services.pipeline.ocr import _assemble_mrz_lines


def _box(
    top_y: float, text: str, confidence: float = 0.9, *, left_x: float = 0
) -> tuple[list[list[float]], str, float]:
    # EasyOCR bboxes are 4 corner points [top-left, top-right, bottom-right, bottom-left].
    # y decides rows; x orders the pieces of one row left to right.
    return (
        [[left_x, top_y], [left_x + 100, top_y], [left_x + 100, top_y + 20], [left_x, top_y + 20]],
        text,
        confidence,
    )


def test_picks_bottom_two_lines_ordered_top_to_bottom() -> None:
    results = [
        _box(50, "SOME HEADER TEXT NOT PART OF THE MRZ AT ALL"),
        _box(300, "L898902C36UTO7408122F1204159<<<<<<<<<<<<<<6"),
        _box(280, "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"),
    ]
    result = _assemble_mrz_lines(results)
    assert result is not None
    line1, line2 = result
    # line1 (y=280) must come before line2 (y=300) despite appearing later in the input list.
    assert line1.startswith("P<UTO")
    assert line2.startswith("L898902C36")


def test_ignores_short_non_mrz_text_boxes() -> None:
    results = [
        _box(10, "PAGE 1"),
        _box(280, "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"),
        _box(300, "L898902C36UTO7408122F1204159<<<<<<<<<<<<<<6"),
    ]
    result = _assemble_mrz_lines(results)
    assert result is not None
    assert len(result[0]) == 44
    assert len(result[1]) == 44


def test_pads_short_line_and_truncates_long_line_to_44_chars() -> None:
    results = [
        # 34 chars -- above the plausibility threshold (30) but still an OCR undershoot of the
        # true 44-char MRZ line, e.g. the last few filler characters weren't detected.
        _box(280, "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<"),
        _box(300, "L898902C36UTO7408122F1204159<<<<<<<<<<<<<<6EXTRA"),  # overshoot
    ]
    result = _assemble_mrz_lines(results)
    assert result is not None
    line1, line2 = result
    assert len(line1) == 44
    assert line1.startswith("P<UTOERIKSSON")
    assert len(line2) == 44
    assert not line2.endswith("EXTRA")


def test_fewer_than_two_plausible_lines_returns_none() -> None:
    results = [_box(280, "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<")]
    assert _assemble_mrz_lines(results) is None


def test_no_detections_returns_none() -> None:
    assert _assemble_mrz_lines([]) is None


def test_merges_a_line_ocr_split_into_pieces_on_one_row() -> None:
    """EasyOCR splits a line at long '<' runs, and returns the pieces in no particular order.
    Observed on rendered data pages by the Phase 13 harness, where it defeated every page."""
    results = [
        _box(700, "<<<<<<", left_x=600),
        _box(702, "P<INDRAHMAN<<ELIF<<<<<<<", left_x=100),
        _box(771, "RKRMJEPVB0IND6608305M3404241<3", left_x=100),
        _box(774, "<<<<8", left_x=700),
    ]
    result = _assemble_mrz_lines(results)
    assert result == (
        "P<INDRAHMAN<<ELIF<<<<<<<<<<<<<".ljust(44, "<"),
        # The short line's longest filler run is widened, so the final check digit lands back
        # in position 44 rather than being stranded mid-line by end padding.
        "RKRMJEPVB0IND6608305M3404241<3<<<<<<<<<<<<<8",
    )
