import datetime as dt

from services.pipeline.extraction.extract import extract_id_document
from services.pipeline.extraction.schemas import IDDocument, VLMExtractionResponse
from tests.pipeline.extraction.test_mrz import _build_td3

VALID_JSON = VLMExtractionResponse(
    document_visible=True,
    document=IDDocument(
        full_name="Jane Doe",
        document_number="X1234567",
        date_of_birth=dt.date(1990, 5, 15),
        expiry_date=dt.date(2030, 5, 15),
        nationality="USA",
        document_type="passport",
    ),
).model_dump_json()

NOT_VISIBLE_JSON = VLMExtractionResponse(document_visible=False, document=None).model_dump_json()


class FakeVLMClient:
    """A queue of canned responses, so tests control exactly what the 'model' returns on each
    call without making a real (paid, non-deterministic, network-dependent) API request."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str | None] = []

    def extract_json(
        self, image_bytes: bytes, mime_type: str, *, error_feedback: str | None = None
    ) -> str:
        self.calls.append(error_feedback)
        return self._responses.pop(0)


def test_valid_extraction_on_first_try_gets_high_confidence() -> None:
    client = FakeVLMClient([VALID_JSON])
    result = extract_id_document(client, b"fake-image-bytes", "image/jpeg", max_retries=3)

    assert result.needs_review is False
    assert result.method == "vlm"
    assert result.document is not None
    assert result.document.full_name == "Jane Doe"
    assert result.confidence == 1.0
    assert len(client.calls) == 1
    assert client.calls[0] is None  # no prior error to feed back on the first attempt


def test_malformed_response_retries_with_error_fed_back_then_succeeds() -> None:
    client = FakeVLMClient(["not valid json at all", VALID_JSON])
    result = extract_id_document(client, b"fake-image-bytes", "image/jpeg", max_retries=3)

    assert result.needs_review is False
    assert result.document is not None
    assert len(client.calls) == 2
    assert client.calls[0] is None
    assert client.calls[1] is not None  # the second call must have received the first error
    # Succeeding on retry is real trust, but strictly less than a clean first-try extraction.
    assert result.confidence < 1.0


def test_document_not_visible_is_treated_as_a_failed_attempt_not_a_valid_empty_result() -> None:
    """Regression test for a real bug found against the live Gemini API: given a blank image,
    the model didn't error -- it returned schema-valid JSON with document_visible=False the
    first time, then a legible response on retry. Before VLMExtractionResponse existed, a
    blank/unreadable image with no escape hatch produced empty strings (then, after adding
    min_length, the literal placeholder "NOT_AVAILABLE") that passed validation outright."""
    client = FakeVLMClient([NOT_VISIBLE_JSON, VALID_JSON])
    result = extract_id_document(client, b"blank-image-bytes", "image/jpeg", max_retries=3)

    assert result.needs_review is False  # recovered on retry with a real document
    assert result.document is not None
    assert len(client.calls) == 2
    assert client.calls[1] is not None  # the retry must have been told *why* it failed


def test_document_not_visible_every_attempt_ends_in_needs_review() -> None:
    client = FakeVLMClient([NOT_VISIBLE_JSON, NOT_VISIBLE_JSON, NOT_VISIBLE_JSON])
    result = extract_id_document(client, b"blank-image-bytes", "image/jpeg", max_retries=3)

    assert result.needs_review is True
    assert result.document is None
    assert result.reason is not None and "document_visible" in result.reason


def test_exhausted_retries_returns_needs_review_not_a_crash() -> None:
    client = FakeVLMClient(["garbage", "still garbage", "still garbage"])
    result = extract_id_document(client, b"fake-image-bytes", "image/jpeg", max_retries=3)

    assert result.needs_review is True
    assert result.document is None
    assert result.confidence == 0.0
    assert result.reason is not None and "exhausted" in result.reason
    assert len(client.calls) == 3  # never called a 4th time -- retries are genuinely bounded


def test_valid_mrz_short_circuits_the_vlm_entirely() -> None:
    line1, line2 = _build_td3()
    client = FakeVLMClient([VALID_JSON])  # would return this if called -- it must not be called

    result = extract_id_document(
        client, b"unused", "image/jpeg", mrz_lines=(line1, line2), max_retries=3
    )

    assert result.method == "mrz"
    assert result.confidence == 0.99
    assert result.document is not None
    assert result.document.document_number == "123456789"
    assert client.calls == []  # the whole point of MRZ-first: no model call needed


def test_missing_mrz_falls_back_to_vlm() -> None:
    client = FakeVLMClient([VALID_JSON])
    result = extract_id_document(
        client, b"fake-image-bytes", "image/jpeg", mrz_lines=None, max_retries=3
    )
    assert result.method == "vlm"
    assert len(client.calls) == 1


def test_invalid_mrz_falls_back_to_vlm_rather_than_trusting_partial_data() -> None:
    line1, line2 = _build_td3()
    corrupted_line2 = ("9" if line2[0] != "9" else "8") + line2[1:]
    client = FakeVLMClient([VALID_JSON])

    result = extract_id_document(
        client, b"fake-image-bytes", "image/jpeg", mrz_lines=(line1, corrupted_line2), max_retries=3
    )

    assert result.method == "vlm"  # fell through to VLM instead of trusting the broken MRZ
    assert len(client.calls) == 1
