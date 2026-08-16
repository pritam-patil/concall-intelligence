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
cp .env.example .env   # then fill in values
```

## Usage

```bash
uv run ingest --help
uv run ingest filings --symbol TCS
uv run ingest transcripts --symbol TCS
```

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

## Layout

```
src/ingest/
  config.py            # env-backed Settings, loaded once via get_settings()
  cli.py                # `ingest` command group (click)
  db.py                 # Supabase client
  providers/
    embeddings.py        # EmbeddingsProvider interface + CloudflareBgeEmbeddings (pinned)
    generation.py         # GenerationProvider interface + GeminiFlashGeneration (pinned)
scripts/
  probe_nse_access.py    # measures NSE access (PDF downloads, seed URLs) — see ../SOURCES.md
```
