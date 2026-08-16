"""Centralized environment configuration, loaded from .env at process start.

Every setting has a single source of truth here so provider swaps (see
`ingest.providers`) only ever touch config values, never call sites.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    # Supabase (Postgres + pgvector), free tier.
    supabase_url: str
    supabase_service_role_key: str

    # Embeddings provider (see ingest.providers.embeddings). Default: Cloudflare Workers AI bge.
    embeddings_provider: str
    cf_account_id: str | None
    cf_api_token: str | None
    cf_embeddings_model: str

    # Generation provider (see ingest.providers.generation). Default: Gemini Flash free tier.
    generation_provider: str
    gemini_api_key: str | None
    gemini_model: str

    # NSE source access.
    nse_base_url: str

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            supabase_url=_require("SUPABASE_URL"),
            supabase_service_role_key=_require("SUPABASE_SERVICE_ROLE_KEY"),
            embeddings_provider=os.environ.get("EMBEDDINGS_PROVIDER", "cloudflare_bge"),
            cf_account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
            cf_api_token=os.environ.get("CLOUDFLARE_API_TOKEN"),
            cf_embeddings_model=os.environ.get(
                "CLOUDFLARE_EMBEDDINGS_MODEL", "@cf/baai/bge-base-en-v1.5"
            ),
            generation_provider=os.environ.get("GENERATION_PROVIDER", "gemini_flash"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY"),
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
            nse_base_url=os.environ.get("NSE_BASE_URL", "https://www.nseindia.com"),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Lazily load and cache Settings so importing this module never requires .env

    to already be populated (e.g. `ingest --help` should work with no env set).
    """
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings
