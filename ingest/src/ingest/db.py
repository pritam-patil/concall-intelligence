"""Supabase client, lazily constructed from settings.

Schema (pgvector) is intentionally not managed here — see
supabase/migrations (SQL) for `documents` / `chunks` tables and the
`match_chunks` RPC used for similarity search.
"""

from __future__ import annotations

from supabase import Client, create_client

from ingest.config import Settings

_client: Client | None = None


def get_client(settings: Settings) -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _client
