from datetime import date

from pydantic import BaseModel, Field


class IDDocument(BaseModel):
    """The structured shape every extraction path (MRZ or VLM) must produce."""

    full_name: str = Field(min_length=1)
    document_number: str = Field(min_length=1)
    date_of_birth: date
    expiry_date: date | None = None
    nationality: str | None = None
    document_type: str = Field(
        min_length=1, description="e.g. passport, national_id, drivers_license"
    )


class VLMExtractionResponse(BaseModel):
    """What we actually ask the VLM for -- gives it an explicit, honest way to say it can't
    read the document, rather than being forced to hallucinate values to satisfy a strict
    schema it has no real answer for.

    This is load-bearing, not decoration -- confirmed against the real API, not assumed: given
    a blank image, Gemini didn't refuse or error. It first returned empty strings for every
    field (which passed plain type validation before min_length constraints existed), and
    after min_length was added, it switched to the literal string "NOT_AVAILABLE" -- which
    trivially passes a non-empty check too. There is no bounded set of placeholder strings to
    defend against; the fix is giving the model a real way to say "no," not chasing its ways
    of saying "I don't know" while still looking valid.
    """

    document_visible: bool = Field(
        description="True only if a legible identity document is actually visible in the image"
    )
    document: IDDocument | None = Field(
        default=None, description="Populated only when document_visible is true"
    )


class ExtractionResult(BaseModel):
    document: IDDocument | None
    confidence: float = Field(ge=0.0, le=1.0)
    method: str = Field(description="mrz | vlm")
    needs_review: bool
    reason: str | None = None
