"""Extraction orchestration: MRZ first (free, checksum-validated), VLM fallback with bounded
retry and error-feedback prompting. See PLAN.md Phase 4.
"""

from pydantic import ValidationError

from .mrz import parse_td3
from .schemas import ExtractionResult, IDDocument, VLMExtractionResponse
from .vlm import VLMClient


def extract_from_mrz(line1: str, line2: str) -> ExtractionResult | None:
    """High-confidence result if the MRZ is present and checksum-valid; None otherwise, so the
    caller falls back to the VLM path rather than trusting a partially-read MRZ."""
    result = parse_td3(line1, line2)
    if not result.valid or result.date_of_birth is None:
        return None
    try:
        document = IDDocument(
            full_name=result.full_name or "",
            document_number=result.document_number or "",
            date_of_birth=result.date_of_birth,
            expiry_date=result.expiry_date,
            nationality=result.nationality,
            document_type=result.document_type or "unknown",
        )
    except ValidationError:
        return None
    return ExtractionResult(document=document, confidence=0.99, method="mrz", needs_review=False)


def extract_with_vlm(
    client: VLMClient, image_bytes: bytes, mime_type: str, *, max_retries: int
) -> ExtractionResult:
    error_feedback: str | None = None
    for attempt in range(1, max_retries + 1):
        raw = client.extract_json(image_bytes, mime_type, error_feedback=error_feedback)
        try:
            response = VLMExtractionResponse.model_validate_json(raw)
        except ValidationError as exc:
            error_feedback = str(exc)
            continue

        if not response.document_visible or response.document is None:
            # Confirmed against the real API: a model asked for a strict schema will fill it
            # with placeholder values rather than admit it can't read the image, unless given
            # this explicit escape hatch (see schemas.VLMExtractionResponse). Treating "no
            # document visible" as a failed attempt -- not a valid empty result -- is the fix.
            error_feedback = (
                "document_visible was false or document was omitted -- if a legible identity "
                "document is genuinely visible, set document_visible to true and populate "
                "document with only what is actually printed on it."
            )
            continue

        # A clean first-try extraction is worth more trust than one the model needed
        # correcting to get right, even though both ultimately pass schema validation.
        confidence = max(0.5, 1.0 - 0.15 * (attempt - 1))
        return ExtractionResult(
            document=response.document, confidence=confidence, method="vlm", needs_review=False
        )

    return ExtractionResult(
        document=None,
        confidence=0.0,
        method="vlm",
        needs_review=True,
        reason=f"exhausted {max_retries} retries; last issue: {error_feedback}",
    )


def extract_id_document(
    client: VLMClient,
    image_bytes: bytes,
    mime_type: str,
    *,
    mrz_lines: tuple[str, str] | None = None,
    max_retries: int = 3,
) -> ExtractionResult:
    if mrz_lines is not None:
        mrz_result = extract_from_mrz(*mrz_lines)
        if mrz_result is not None:
            return mrz_result
    return extract_with_vlm(client, image_bytes, mime_type, max_retries=max_retries)
