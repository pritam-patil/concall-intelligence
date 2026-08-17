"""Embeddings provider interface.

Pinned default: Cloudflare Workers AI `@cf/baai/bge-base-en-v1.5` (free tier,
768-dim). Swapping providers means adding a class here and pointing
`EMBEDDINGS_PROVIDER` at it — nothing else in the pipeline changes.

`gemini` is a fallback, not a second pinned choice — for when Cloudflare's
free tier is unavailable or exhausted, switched to manually (flip
EMBEDDINGS_PROVIDER, re-run ingest.embed), not automatic mid-run failover.
Deliberately: a chunk's embedding is only comparable by cosine distance to
another embedding from the SAME model, so silently mixing providers within
one embed run would put two incompatible vector spaces in one column with
no way to tell them apart later (see `chunks.embedding_provider` and
ingest/src/ingest/embed.py, which is what actually enforces this — this
module just makes both providers callable, not which one runs when).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ingest.config import Settings


class EmbeddingsProvider(ABC):
    """Turns text chunks into fixed-size vectors for pgvector storage/search."""

    dimensions: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input in order."""
        raise NotImplementedError


class CloudflareBgeEmbeddings(EmbeddingsProvider):
    """Cloudflare Workers AI bge-base-en-v1.5. Free tier: 10k neurons/day."""

    dimensions = 768

    def __init__(self, account_id: str, api_token: str, model: str) -> None:
        self._url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        )
        self._headers = {"Authorization": f"Bearer {api_token}"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def embed(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=30) as client:
            resp = client.post(self._url, headers=self._headers, json={"text": texts})
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"Cloudflare embeddings call failed: {data.get('errors')}")
            return data["result"]["data"]


class GeminiEmbeddings(EmbeddingsProvider):
    """Fallback: Gemini's embedding API (gemini-embedding-001), pinned to
    768 output dimensions to match Cloudflare bge and `vector(768)`.

    Two things confirmed by calling the real endpoint while building this,
    not assumed from docs:
      - The model's native output is 3072-dim; requesting 768 via
        `outputDimensionality` (Matryoshka truncation) is what gets it down
        to size, not a separate smaller model.
      - Truncated output is NOT unit-normalized (~0.58 norm observed, vs.
        bge's ~1.0) — Google's own docs say to renormalize truncated
        embeddings, so embed() does that before returning. Cosine distance
        (what match_chunks uses) is scale-invariant, so this wouldn't have
        broken search either way, but leaving vectors at a norm that
        doesn't mean anything is worse than fixing it for a few lines.
    """

    dimensions = 768

    def __init__(self, api_key: str, model: str) -> None:
        self._url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
        )
        self._model = f"models/{model}"
        self._api_key = api_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def embed(self, texts: list[str]) -> list[list[float]]:
        requests = [
            {
                "model": self._model,
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": self.dimensions,
            }
            for t in texts
        ]
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                self._url, params={"key": self._api_key}, json={"requests": requests}
            )
            resp.raise_for_status()
            data = resp.json()
            vectors = [e["values"] for e in data["embeddings"]]
            return [_l2_normalize(v) for v in vectors]


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = sum(v * v for v in vector) ** 0.5
    return [v / norm for v in vector] if norm else vector


def get_embeddings_provider(settings: Settings) -> EmbeddingsProvider:
    if settings.embeddings_provider == "cloudflare_bge":
        if not settings.cf_account_id or not settings.cf_api_token:
            raise RuntimeError(
                "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required for "
                "EMBEDDINGS_PROVIDER=cloudflare_bge"
            )
        return CloudflareBgeEmbeddings(
            account_id=settings.cf_account_id,
            api_token=settings.cf_api_token,
            model=settings.cf_embeddings_model,
        )
    if settings.embeddings_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required for EMBEDDINGS_PROVIDER=gemini")
        return GeminiEmbeddings(
            api_key=settings.gemini_api_key, model=settings.gemini_embeddings_model
        )
    raise ValueError(f"Unknown embeddings provider: {settings.embeddings_provider!r}")
