"""Text-embedding client, behind a swappable interface -- same reasoning as
services/pipeline/extraction/vlm.py: Gemini is the active provider, but nothing else in this
package talks to Gemini directly, so swapping providers later is one new adapter class.
"""

import ssl
from typing import Protocol

import httpx
import truststore
from google import genai
from google.genai import types


def _truststore_http_options() -> types.HttpOptions:
    """Same scoped fix as vlm.py -- truststore.inject_into_ssl() patches ssl.SSLContext
    process-wide and previously broke boto3/botocore's unrelated S3 client construction when
    tried that way. Scoping it to this client's own httpx.Client avoids that entirely.
    """
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return types.HttpOptions(httpx_client=httpx.Client(verify=ssl_context))


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]: ...


class GeminiEmbeddingClient:
    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        self._client = genai.Client(api_key=api_key, http_options=_truststore_http_options())
        self._model = model
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        response = self._client.models.embed_content(
            model=self._model,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=self._dimensions),
        )
        if not response.embeddings:
            raise RuntimeError(f"Gemini returned no embeddings for {text!r}")
        values = response.embeddings[0].values
        if values is None:
            raise RuntimeError(f"Gemini returned an empty embedding for {text!r}")
        return list(values)
