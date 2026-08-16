# ingest

Python ingestion pipeline: fetch NSE filings and earnings-call transcripts,
parse, chunk, embed, and store in Supabase (pgvector). See
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the overall design.

## Setup

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync
cp .env.example .env   # then fill in values
```

Or with plain pip:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .   # so `ingest.*` and the `ingest` command are importable/runnable
cp .env.example .env   # then fill in values
```

## Usage

```bash
uv run ingest --help
uv run ingest filings --symbol TCS
uv run ingest transcripts --symbol TCS
uv run ingest download                     # every seed document in ingest/seeds.py
uv run ingest download --symbol TCS --symbol INFY   # just these
```

`filings`/`transcripts` are stubs for live discovery (not built yet — they'd
call the same announcements/annual-reports APIs `scripts/probe_nse_access.py`
already probes). `download` is real: it ingests the fixed, curated document
list in [`src/ingest/seeds.py`](src/ingest/seeds.py) — see
[Downloading documents](#downloading-documents) below.

## Connecting to Supabase

Schema lives in [`../supabase/migrations/`](../supabase/migrations/) (a
standard Supabase CLI project — `../supabase/config.toml`, one baseline
migration, `../supabase/seed.sql`), not in this package. This section is how
to actually apply it to a real project.

### 1. Get the CLI

No package-manager install is assumed to work — on this machine Homebrew's
bottle needed newer Xcode Command Line Tools than were installed, so the
CLI was fetched directly instead:

```bash
# macOS/Linux, no brew required:
curl -sL "https://github.com/supabase/cli/releases/latest/download/supabase_$(uname -s | tr A-Z a-z)_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" \
  | tar xz -C /usr/local/bin supabase
supabase --version
```

(Or `brew install supabase/tap/supabase` if your Homebrew's CLT is current.)

### 2. Authenticate and link

Linking needs two secrets the CLI has never been given in this repo — an
**access token** (account-level, for the Management API) and the project's
**database password** (for the direct Postgres connection). Neither is the
Supabase anon/service-role key in `web/.env.local` — those are API keys, not
CLI credentials.

```bash
# Personal access token: https://supabase.com/dashboard/account/tokens
export SUPABASE_ACCESS_TOKEN=sbp_...
# or: supabase login

# Project ref is the subdomain in NEXT_PUBLIC_SUPABASE_URL
# (web/.env.local — https://<ref>.supabase.co).
# DB password: Project Settings → Database → Connection string, or reset it
# there if it's been lost — Supabase does not display it after creation.
supabase link --project-ref <ref> --password <db-password>
```

Without both, `supabase link` fails fast with
`LegacyPlatformAuthRequiredError` rather than hanging or falling back to
something partial — confirmed by running it credential-less against this
project's own ref.

### 3. Apply the schema

```bash
supabase db push               # runs supabase/migrations/*.sql
supabase db push --include-seed   # ...and supabase/seed.sql in one shot
```

To (re-)seed on its own against an already-migrated project:

```bash
supabase db query --linked -f ../supabase/seed.sql
```

`seed.sql` is an `ON CONFLICT ... DO UPDATE`, so re-running it is safe.

### 4. Local dev instead (optional)

`supabase start` runs the full stack (Postgres, Studio, Storage, ...) in
Docker, entirely offline from the hosted project — useful for iterating on
migrations without touching real data. Requires Docker; not covered further
here since this project's ₹0 plan is the hosted free tier, not a local
stack.

### What's actually been verified, and what hasn't

The migration and seed were run and passed against a real Postgres 17 +
pgvector instance (Homebrew-installed, throwaway data directory, torn down
after) — `create extension`, both tables, the `doc_type` enum, both indexes
(confirmed via `\di+` as `hnsw` and `gin`, not just "didn't error"),
`match_chunks`/`search_chunks_text` (inserted a test chunk, called both,
got a real similarity/rank back), the cascade delete, and the unique
constraint on `nse_seq_id`. What was **not** run is `supabase link` /
`db push` against the actual hosted project — that needs the access token
and DB password from step 2, which this environment doesn't have. Treat the
SQL as tested, not the live deploy.

This is also why `download` (below) fails against the real project today —
it needs `documents` to exist, and step 3 hasn't been run there yet.

## Downloading documents

`ingest download` walks [`src/ingest/seeds.py`](src/ingest/seeds.py) (the
curated list transcribed from [`../SOURCES.md`](../SOURCES.md) — six recent
concall transcripts, six FY2025-26 annual reports), and for each one:

1. If the seed carries an `nse_seq_id` (concall entries do; annual reports
   don't — NSE's `/api/annual-reports` feed has no such field), checks
   `documents.nse_seq_id` first and skips the download entirely on a hit.
2. Downloads the PDF through a primed session (`ingest.nse_fetch` —
   section-page cookie priming, `Accept-Encoding: gzip, deflate`, a
   browser-like User-Agent, redirects followed) and computes its sha256.
3. Checks `documents.sha256` and skips (no upload, no insert) on a hit —
   this is what catches annual reports (no seq_id to pre-check) on a re-run,
   and guards against the same bytes appearing at a different URL.
4. Otherwise uploads the original to the `filings` Storage bucket at
   `{symbol}/{doc_type}/{filename}` and inserts a `documents` row —
   `nse_seq_id` set for concall entries, null for annual reports.

One line is logged per document either way — `ingested`, `skip (...)`, or
`ERROR — ...`. Requests are paced `DOWNLOAD_PACE_SECONDS` (1.5s) apart —
see `ingest.nse_fetch` for why: no rate-limiting was observed toward
`nsearchives.nseindia.com` at that pace in SOURCES.md §1, and downloading
more files isn't a reason to push harder against someone else's free
infrastructure.

**Verified for real**, in two halves, since the live project doesn't have
`documents` yet (see above):

- **Storage half, against the real hosted project**: `ensure_bucket()` and
  a real `.upload()` were both run for real — the `filings` bucket exists
  (private) on the live project, and one real seed document (RELIANCE's
  concall transcript, a genuine live download) is sitting in it at
  `filings/RELIANCE/concall/kavinavora_19072026180618_SE_Transcript.pdf`,
  confirmed present via the Storage API afterward. This is real seed data
  now, not test pollution — it wasn't cleaned up.
- **Database half, against a local stand-in**: since bare PostgREST (a
  single downloadable binary — no Docker needed) is the same REST layer
  `supabase-py`'s `.table()` calls speak, it was pointed at a local scratch
  Postgres with the migration and seed applied, and `ingest_one()` was run
  against it for real (real NSE downloads, real inserts) with only
  `.storage` swapped for a recording fake. Confirmed: a new document
  ingests correctly; a re-run with the same `nse_seq_id` skips without
  downloading; a same-content document under a different `nse_seq_id`
  skips via `sha256` instead; an annual-report-shaped doc (no seq_id)
  ingests and then correctly dedupes by `sha256` alone on a second run; a
  404 URL returns `"error"` without uploading or inserting anything.
- **Against the actual hosted project**: running `ingest download --symbol
  RELIANCE` for real does exactly what step 3 above predicts — the bucket
  step succeeds, then it fails immediately on the first document with
  `Could not find the table 'public.documents' in the schema cache`
  (`PGRST205`). This is the expected, informative failure, not a bug —
  push the schema (this page, above) and it'll run clean.

## Layout

```
src/ingest/
  config.py            # env-backed Settings, loaded once via get_settings()
  cli.py                # `ingest` command group (click) — filings/transcripts (stubs), download
  db.py                 # Supabase client
  nse_fetch.py           # shared NSE session/probe/fetch — ported verbatim from nse-assist
  seeds.py                # the curated SEED_DOCUMENTS list, transcribed from ../SOURCES.md
  download.py              # downloads seeds.py, hashes, uploads to Storage, records in `documents`
  providers/
    embeddings.py        # EmbeddingsProvider interface + CloudflareBgeEmbeddings (pinned)
    generation.py         # GenerationProvider interface + GeminiFlashGeneration (pinned)
scripts/
  probe_nse_access.py    # measures NSE access (PDF downloads, seed URLs) — see ../SOURCES.md
```
