"""Text-embedding client, behind a swappable interface -- same reasoning as
services/pipeline/extraction/vlm.py: nothing else in this package talks to a provider directly,
so swapping providers is one new adapter class.

Since Phase 13 the default is a local ONNX model via fastembed (D26), not Gemini: the real OFAC
list is ~16,000 individual names and aliases, every evaluation run embeds thousands more, and a
free-tier API makes both slow, rate-limited and unrepeatable. Gemini stays selectable with
EMBEDDING_PROVIDER=gemini.
"""

import ssl
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

import httpx
import truststore
from google import genai
from google.genai import types

if TYPE_CHECKING:
    from .config import Settings


def _truststore_http_options() -> types.HttpOptions:
    """Same scoped fix as vlm.py -- truststore.inject_into_ssl() patches ssl.SSLContext
    process-wide and previously broke boto3/botocore's unrelated S3 client construction when
    tried that way. Scoping it to this client's own httpx.Client avoids that entirely.
    """
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return types.HttpOptions(httpx_client=httpx.Client(verify=ssl_context))


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]: ...


class FastEmbedClient:
    """Local ONNX sentence-embedding model. The model files (~220 MB for the default) download
    once into cache_dir on first construction, which is why construction is lazy everywhere."""

    def __init__(self, model: str, *, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model, cache_dir=cache_dir)
        self.model_name = model

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: Sequence[str], *, batch_size: int = 64) -> list[list[float]]:
        """Batched: ingest and evaluation embed thousands of names, and one ONNX call per name
        is roughly an order of magnitude slower than batches."""
        return [v.tolist() for v in self._model.embed(list(texts), batch_size=batch_size)]


class CharNgramEmbeddingClient:
    """Character n-grams of each name token, feature-hashed into a fixed-size vector -- the
    classic representation for name matching, and no model at all.

    A sentence-embedding model is trained on meaning, and a name has very little: measured on
    the real OFAC list, a paraphrase model scored "Priya Sharma" 0.79 against an unrelated
    listed name and missed "Aiman Zawahri" -> "Ayman al-Zawahiri" from its top three. Shared
    character n-grams track spelling, which is what transliteration, typos and reordered tokens
    actually preserve. Each token is padded with "#" so a token's first and last letters carry
    more weight than its middle, and tokens are hashed independently, so token order is free.

    Signed hashing (each n-gram adds +1 or -1 depending on one hash bit) keeps hash collisions
    from inflating similarity on average. Deterministic and free to run.
    """

    model_name = "char-ngram-2-3"

    def __init__(self, dimensions: int, ngram_sizes: tuple[int, ...] = (2, 3)) -> None:
        self._dimensions = dimensions
        self._ngram_sizes = ngram_sizes

    def embed(self, text: str) -> list[float]:
        import hashlib

        import numpy as np

        vector = np.zeros(self._dimensions, dtype=np.float64)
        for token in text.split():
            padded = f"#{token}#"
            for n in self._ngram_sizes:
                for i in range(len(padded) - n + 1):
                    digest = hashlib.blake2b(padded[i : i + n].encode("utf-8"), digest_size=8)
                    h = int.from_bytes(digest.digest(), "little")
                    vector[h % self._dimensions] += 1.0 if h >> 63 else -1.0
        norm = float(np.linalg.norm(vector))
        return (vector / norm).tolist() if norm else vector.tolist()


def embed_many(client: EmbeddingClient, texts: Sequence[str]) -> list[list[float]]:
    """Uses the client's batch path when it has one, so callers need not care which it is."""
    batch = getattr(client, "embed_many", None)
    if batch is not None:
        return list(batch(texts))
    return [client.embed(t) for t in texts]


def embedding_model_version(settings: "Settings") -> str:
    """What audit rows and eval runs record as model_version for a screen."""
    if settings.embedding_provider == "gemini":
        return f"{settings.gemini_embedding_model}@{settings.embedding_dimensions}"
    if settings.embedding_provider == "ngram":
        return f"{CharNgramEmbeddingClient.model_name}@{settings.embedding_dimensions}"
    return settings.fastembed_model


def build_embedding_client(settings: "Settings") -> EmbeddingClient:
    if settings.embedding_provider == "gemini":
        return GeminiEmbeddingClient(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    if settings.embedding_provider == "ngram":
        return CharNgramEmbeddingClient(settings.embedding_dimensions)
    return FastEmbedClient(settings.fastembed_model, cache_dir=settings.model_cache_dir)


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
