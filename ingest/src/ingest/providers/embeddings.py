"""Embeddings provider interface.

Pinned default: Cloudflare Workers AI `@cf/baai/bge-base-en-v1.5` (free tier,
768-dim). Swapping providers means adding a class here and pointing
`EMBEDDINGS_PROVIDER` at it — nothing else in the pipeline changes.
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
    raise ValueError(f"Unknown embeddings provider: {settings.embeddings_provider!r}")
