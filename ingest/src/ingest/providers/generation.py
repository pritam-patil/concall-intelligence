"""Generation (answer synthesis) provider interface.

Pinned default: Gemini Flash, free tier. Swapping providers means adding a
class here and pointing `GENERATION_PROVIDER` at it — the retrieval/prompting
code above this layer does not change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ingest.config import Settings


class GenerationProvider(ABC):
    """Takes a prompt (question + retrieved, source-cited context) and returns an answer."""

    @abstractmethod
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        raise NotImplementedError


class GeminiFlashGeneration(GenerationProvider):
    """Gemini Flash via the Generative Language API free tier."""

    def __init__(self, api_key: str, model: str) -> None:
        self._url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        )
        self._api_key = api_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        payload: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                self._url,
                params={"key": self._api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]


def get_generation_provider(settings: Settings) -> GenerationProvider:
    if settings.generation_provider == "gemini_flash":
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required for GENERATION_PROVIDER=gemini_flash")
        return GeminiFlashGeneration(api_key=settings.gemini_api_key, model=settings.gemini_model)
    raise ValueError(f"Unknown generation provider: {settings.generation_provider!r}")
