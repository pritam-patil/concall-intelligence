---
name: run-ingestion
description: Run the download → extract → chunk → embed pipeline (ingest/) for one or more symbols and sanity-check the result — chunk counts, token averages, a real similarity query. Use whenever ingest/ code changes or new symbols/documents need loading.
---

# Running the ingestion pipeline

## Command

Check [`ingest/src/ingest/cli.py`](../../../ingest/src/ingest/cli.py) for
the current subcommand names before running — they've been added
incrementally across sessions and this doc can drift:

```bash
cd ingest
uv run ingest download --symbol TCS --symbol INFY   # or omit --symbol for every seeded document
```

`download` alone only fetches+stores raw documents; the full
download → extract → chunk → embed pipeline is wired in
[`ingest/src/ingest/run.py`](../../../ingest/src/ingest/run.py) with a
`--symbol` flag — confirm the exact invocation `cli.py` exposes for it
before assuming a flag name.

## Before running: check quota state

Two independent free-tier limits gate this, and both have been genuinely
exhausted by this project's own testing before — check state, don't just
run and hope:

- **Cloudflare Workers AI**: 10,000 neurons/day, shared across the whole
  account. A 429 with "you have used up your daily free allocation" means
  wait for the daily reset, not retry harder.
- **Gemini embeddings** (`EMBEDDINGS_PROVIDER=gemini` fallback): a separate,
  tighter **per-minute** limit. Batch with pacing (~12s between batches of
  ~20) and batch-level backoff (base 15s, doubling, capped retries) *on top
  of* `embed.py`'s per-call `tenacity` retry — two layers, not one.

If Cloudflare is exhausted mid-task, don't silently fail the task — flip
`EMBEDDINGS_PROVIDER=gemini` (in `ingest/.env`, and in `web/.env.local` too
if `web/` is also being tested) and say so out loud; it's a real, designed
fallback path, not a workaround.

## Resumability

Both `download.py` and `embed.py` are resumable by design — re-running
after a partial failure is always safe:

- `download.py` skips a document if `documents.nse_seq_id` (concalls) or
  `documents.sha256` (all doc types — catches annual reports, which have no
  seq_id) already matches.
- `embed.py` skips a chunk only if it already has an embedding **from the
  same provider** (`chunks.embedding_provider` column) — "has an embedding"
  alone is not "done"; a Gemini-embedded chunk is not interchangeable with
  a `cloudflare_bge` one for ranking, so don't skip on vector presence
  alone.
- Storage uploads use `x-upsert: true`, so re-running after a partial
  failure won't 409 on files already uploaded.

## After running: sanity-check for real

Run [`ingest/sanity.sql`](../../../ingest/sanity.sql) (chunk counts per
document, avg tokens/chunk, and a similarity query — "management commentary
on margins" is the canonical test query used before) against whichever DB
was targeted, and append real results to `ingest/NOTES.md`. Never report a
run as successful without pasting real counts — a silent zero-row insert or
a provider returning an empty batch looks identical to success in the CLI's
log lines otherwise.
