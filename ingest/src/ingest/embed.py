"""Batch-embeds chunks (ingest.chunk's output) and upserts them into
`chunks`, with pgvector.

    uv run python -m ingest.embed --in chunks.jsonl

Reads {id, document_id, page, section, content, token_count} rows
(chunk.py's JSONL shape), embeds `content` in batches through whichever
EmbeddingsProvider is configured (EMBEDDINGS_PROVIDER — see
ingest.providers.embeddings), and upserts each row into `chunks` with its
embedding and embedding_provider set. `embed_chunks(chunks)` is the
reusable core if a caller already has rows in memory (ingest.run does,
straight from ingest.chunk) — `run(in_path)` is a thin JSONL-reading
wrapper around it, only for the CLI.

ID BRIDGING. chunk.py's `id` is a sha256 hex string, not a uuid — see its
own docstring, which explicitly leaves this as "the storage step's
problem". This is that step: `chunk_uuid()` below deterministically maps
that hex string onto a uuid5, so the same chunk always upserts to the same
row (name-based, not random — see its docstring for why uuid5 specifically,
not a truncated-bytes hack).

RESUMABILITY. Before embedding anything, every chunk's mapped uuid is
checked against the DB in one query: already has a non-null embedding AND
embedding_provider matches the one running now -> skip it, no API call
spent. Embedded-by-a-different-provider is deliberately NOT treated as
done — see the embedding_provider migration's comment for why silently
skipping that would be wrong, not just imprecise. A run interrupted
partway through (crash, rate limit, Ctrl-C) can just be re-run: whatever
already landed in the DB is skipped, whatever didn't gets embedded.

RATE LIMITS. Two layers, not one:
  - Each EmbeddingsProvider.embed() call already retries itself (tenacity,
    3 attempts, exponential backoff) — see ingest.providers.embeddings.
  - This module adds a second layer ABOVE that: if a whole BATCH still
    fails after the provider's own retries are exhausted, embed_batch()
    retries the batch itself (MAX_BATCH_RETRIES, exponential backoff) —
    failures here are almost always the free-tier daily quota being
    momentarily unhappy about burst size, not the sort of thing worth
    aborting a multi-hundred-chunk run over. A batch that's still failing
    after that is logged as failed and the run moves on — see run()'s
    summary counts, not a silent drop.
  - Batches are also paced EMBED_PACE_SECONDS apart regardless of success,
    matching the pacing pattern in ingest.nse_fetch (a courtesy delay
    toward someone else's free infrastructure, not a measured requirement
    — see that module for the fuller reasoning).
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from ingest.config import get_settings
from ingest.db import get_client
from ingest.providers.embeddings import get_embeddings_provider

EMBED_BATCH_SIZE = 50  # confirmed working up to 150 texts/call against Cloudflare during testing
EMBED_PACE_SECONDS = 1.0
MAX_BATCH_RETRIES = 3
BATCH_BACKOFF_BASE_SECONDS = 5.0

# A fixed, arbitrary namespace UUID for this project's chunk ids specifically
# — generated once (uuid.uuid4()) and hardcoded so chunk_uuid() is stable
# across processes and time, the same reason a namespace exists in RFC 4122
# at all. Never regenerate this value; every existing chunk uuid depends on it.
_CHUNK_ID_NAMESPACE = uuid.UUID("6a6e2b0e-6b7b-4f34-9f2e-9d6b6f5c8e3d")


def chunk_uuid(chunk_id_hex: str) -> str:
    """chunk.py's sha256-hex id -> a uuid5, deterministically. Name-based
    (uuid5), not a truncate-the-hash-to-16-bytes hack: uuid5 is the
    standard-library, spec-compliant way to get "the same name always
    produces the same uuid" without hand-rolling version/variant bits."""
    return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, chunk_id_hex))


def read_chunks_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def already_embedded_uuids(client, uuids: list[str], provider_name: str) -> set[str]:
    """uuids that already have a non-null embedding from THIS provider —
    resumability's skip set. Checked in one query, not one per chunk."""
    if not uuids:
        return set()
    result = (
        client.table("chunks")
        .select("id")
        .in_("id", uuids)
        .eq("embedding_provider", provider_name)
        .not_.is_("embedding", "null")
        .execute()
    )
    return {row["id"] for row in result.data}


def embed_batch_with_backoff(provider, texts: list[str]) -> list[list[float]]:
    """provider.embed(texts), retrying the WHOLE BATCH if it still fails
    after the provider's own internal retries. See module docstring for
    why this is a second, coarser layer rather than the only one."""
    last_exc = None
    for attempt in range(MAX_BATCH_RETRIES):
        if attempt:
            time.sleep(BATCH_BACKOFF_BASE_SECONDS * (2**(attempt - 1)))
        try:
            return provider.embed(texts)
        except Exception as exc:  # noqa: BLE001 - re-raised as last_exc below if retries exhaust
            last_exc = exc
    raise last_exc


def upsert_chunks(client, rows: list[dict]) -> None:
    client.table("chunks").upsert(rows, on_conflict="id").execute()


def embed_chunks(chunks: list[dict], *, label: str = "chunks") -> dict[str, int]:
    """The reusable core: chunk.py-shaped rows (in memory — no JSONL
    round-trip required) -> embedded and upserted into `chunks`.
    `run()` below is a thin JSONL-reading wrapper around this; ingest.run
    (the download -> extract -> chunk -> embed orchestrator) calls this
    directly with rows it already has in memory, for the same reason."""
    started = time.monotonic()
    settings = get_settings()
    client = get_client(settings)
    provider = get_embeddings_provider(settings)
    provider_name = settings.embeddings_provider

    for c in chunks:
        c["uuid"] = chunk_uuid(c["id"])

    skip_ids = already_embedded_uuids(client, [c["uuid"] for c in chunks], provider_name)
    to_embed = [c for c in chunks if c["uuid"] not in skip_ids]
    counts = {"total": len(chunks), "skipped": len(chunks) - len(to_embed), "embedded": 0, "failed": 0}
    print(
        f"[embed] {label}: {counts['total']} chunk(s), "
        f"{counts['skipped']} already embedded ({provider_name}), "
        f"{len(to_embed)} to embed"
    )

    for batch_num, start in enumerate(range(0, len(to_embed), EMBED_BATCH_SIZE)):
        batch = to_embed[start : start + EMBED_BATCH_SIZE]
        if batch_num > 0:
            time.sleep(EMBED_PACE_SECONDS)

        try:
            vectors = embed_batch_with_backoff(provider, [c["content"] for c in batch])
        except Exception as exc:  # noqa: BLE001 - logged and counted, run continues
            counts["failed"] += len(batch)
            print(f"[embed] batch {batch_num}: FAILED ({len(batch)} chunks) — {exc}")
            continue

        rows = [
            {
                "id": c["uuid"],
                "document_id": c["document_id"],
                "page": c["page"],
                "section": c["section"],
                "content": c["content"],
                "token_count": c["token_count"],
                "embedding": vector,
                "embedding_provider": provider_name,
            }
            for c, vector in zip(batch, vectors, strict=True)
        ]
        try:
            upsert_chunks(client, rows)
        except Exception as exc:  # noqa: BLE001 - logged and counted, run continues
            counts["failed"] += len(batch)
            print(f"[embed] batch {batch_num}: upsert FAILED ({len(batch)} chunks) — {exc}")
            continue

        counts["embedded"] += len(batch)
        print(f"[embed] batch {batch_num}: {len(batch)} embedded and upserted")

    elapsed = time.monotonic() - started
    print(
        f"[embed] done: {counts['embedded']} embedded, {counts['skipped']} skipped, "
        f"{counts['failed']} failed, out of {counts['total']}, in {elapsed:.1f}s"
    )
    return counts


def run(in_path: Path) -> dict[str, int]:
    return embed_chunks(read_chunks_jsonl(in_path), label=str(in_path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="in_path", required=True, type=Path)
    args = parser.parse_args(argv)

    counts = run(args.in_path)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
