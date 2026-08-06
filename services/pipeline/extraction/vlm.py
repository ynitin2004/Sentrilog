"""VLM structured-extraction client, behind a swappable interface.

Gemini is the active provider (free tier for development). GeminiVLMClient is the only thing
that needs to change to swap providers later (e.g. to GPT-4o) -- nothing in extract.py talks
to Gemini directly.
"""

import ssl
from typing import Protocol

import httpx
import truststore
from google import genai
from google.genai import types

from .schemas import VLMExtractionResponse


def _truststore_http_options() -> types.HttpOptions:
    """Corporate/AV TLS-inspecting proxies (e.g. Kaspersky) inject their own root certificate,
    which certifi's bundled CA list doesn't trust -- outbound HTTPS calls to Gemini's API fail
    with CERTIFICATE_VERIFY_FAILED otherwise. truststore delegates verification to the OS trust
    store instead (which *does* trust it), rather than weakening verification with verify=False.

    Deliberately scoped to just this client's own httpx.Client, not truststore.inject_into_ssl()
    -- that patches ssl.SSLContext *process-wide*, which broke boto3/botocore's unrelated S3
    client construction (RecursionError in its own SSL setup) when both were tried together.
    A global monkeypatch for a proxy-cert problem that only affects real external HTTPS calls
    (Gemini) has no business touching local Postgres/MinIO connections that never hit it.
    """
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return types.HttpOptions(httpx_client=httpx.Client(verify=ssl_context))


_EXTRACTION_PROMPT = (
    "Look at this image. If it does not contain a legible identity document (e.g. it's blank, "
    "unrelated, or too illegible to read), set document_visible to false and omit document -- "
    "do not invent placeholder values to fill the fields. If a legible document IS visible, "
    "set document_visible to true and extract only what is actually printed on it; do not "
    "guess or infer a value that isn't visible. date_of_birth and expiry_date must be "
    "ISO 8601 (YYYY-MM-DD)."
)


class VLMClient(Protocol):
    def extract_json(
        self, image_bytes: bytes, mime_type: str, *, error_feedback: str | None = None
    ) -> str:
        """Returns raw JSON text matching VLMExtractionResponse. error_feedback, when given, is
        the validation error from the previous attempt, fed back so the model can self-correct."""
        ...


class GeminiVLMClient:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key, http_options=_truststore_http_options())
        self._model = model

    def extract_json(
        self, image_bytes: bytes, mime_type: str, *, error_feedback: str | None = None
    ) -> str:
        prompt = _EXTRACTION_PROMPT
        if error_feedback:
            prompt += (
                f"\n\nYour previous response failed validation with this error: "
                f"{error_feedback}\nCorrect it and try again."
            )

        # google-genai's `contents` parameter type is a deeply nested Union of Lists that mypy
        # can't resolve a mixed [Part, str] literal against (list invariance defeats its usual
        # subtyping check here) -- this is a real limitation in the SDK's type signature, not a
        # bug in this call: it's the exact usage shown in Google's own docs, and proven working
        # against the live API (see scripts/smoke_test_gemini_extraction.py).
        response = self._client.models.generate_content(
            model=self._model,
            contents=[  # type: ignore[arg-type]
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VLMExtractionResponse,
            ),
        )
        if response.text is None:
            raise RuntimeError("Gemini returned an empty response")
        return response.text
