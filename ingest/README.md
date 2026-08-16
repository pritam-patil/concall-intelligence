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

For tests/lint (`pytest`, `ruff`, `mypy`): `uv sync --extra dev`, or plain-pip
`pip install -e ".[dev]"`.

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

## Extracting text

`ingest.extract.extract_pdf(path, document_id)` turns one PDF into a list of
`{"document_id", "page", "text"}` rows (`page` is 1-indexed), via PyMuPDF.
CLI:

```bash
uv run python -m ingest.extract --pdf path/to.pdf --document-id <uuid> --out rows.jsonl
```

Two things it does beyond a bare `page.get_text()`:

- **Two-column layouts.** PyMuPDF's raw block order follows the PDF's
  drawing order, not visual reading order — on a two-column page that
  commonly interleaves left/right text mid-sentence. When a page's text
  blocks split cleanly across the horizontal midline (no block crosses it,
  and both halves have enough blocks to be confident it's really two
  columns and not noise), every left-column block is ordered top-to-bottom
  before any right-column block. Anything else — single column, a table, a
  block spanning the middle — falls back to a plain top-to-bottom sort.
- **Header/footer stripping**, heuristic and explicitly imperfect: a short
  block (a page number, a running title) inside a **fixed-point** margin
  band at the top/bottom of the page is dropped outright; a longer block in
  that same band is only dropped if its digit-normalized text repeats
  across multiple pages of the document (the signature of a running
  footer, not a heading that happens to sit near a margin on one page). A
  point margin, not a fraction of page height, is deliberate — see the
  module docstring for the false positive (a genuine subheading) that an
  8%-of-height band produced on the real annual-report fixture.

Kept blocks are joined with a **blank line** between them, not a single
`\n` — a PyMuPDF block's own text often has internal line-wraps (a bullet
wrapping across three lines is one block, one string, with plain `\n`
between its own visual lines), so a single `\n` between blocks would make
an in-block wrap indistinguishable from an actual block boundary.
`ingest.chunk`'s section/heading detection depends on telling the two
apart — see [Chunking text](#chunking-text) below.

(The real annual-report fixture turned out to be a two-printed-page
spread rendered as one wide landscape PDF page — page 36 on the left half,
page 37 on the right, each with its own footer at the same height. Fine
either way: the two-column heuristic doesn't know or care whether the
left/right split it's ordering is one article's two columns or two
separate printed pages side by side — it's the same problem, and both
footers get stripped the same way either half they're on.)

### Tests

```bash
uv run pytest              # or: .venv/bin/pytest, from ingest/
```

`tests/fixtures/annual_report_page.pdf` and `transcript_page.pdf` are each
one real page — not synthesized — cut with PyMuPDF itself from documents
actually in `seeds.py`: page 37 of INFY's FY2025-26 annual report (a
genuine two-column bullet list, confirmed via a full-document layout scan
before picking it — 24 left blocks, 49 right) and page 3 of RELIANCE's
concall transcript (clean single column, a real repeating-style footer).
`tests/test_extract.py` asserts real phrases from each land on `page: 1`,
that a phrase split across a line break within one bullet stays contiguous
(what breaks if column detection fails), that left-column bullets keep
their original order ahead of right-column content, and that each page's
footer is gone from the output. A few additional unit tests cover the two
heuristics on synthetic input, including one case neither fixture can
exercise on its own (a single page has no other page to cross-check a
repeating footer against).

## Chunking text

`ingest.chunk.chunk_page(document_id, page, text)` splits one page's text
(`extract.py`'s output) into overlapping chunks:
`{id, document_id, page, section, content, token_count}` — everything
`chunks` (`supabase/migrations/`) needs except `embedding`, added later by
a separate embed step. CLI, reading `extract.py`'s JSONL and writing
chunk rows:

```bash
uv run python -m ingest.chunk --in pages.jsonl --out chunks.jsonl
```

- **Target size ~800 tokens, ~100-token overlap.** "Token" is a plain
  whitespace word count, not either embeddings provider's real tokenizer —
  a sizing heuristic only has to hit "~800", not an exact limit, and a real
  tokenizer (tiktoken's vocab files come from a CDN on first use) is
  unwarranted for that. Chunks accumulate whole sentences up to the
  target; overlap re-includes whichever trailing sentences of a chunk sum
  to at least 100 tokens as the start of the next one, so overlap is
  always a round number of complete sentences too.
- **Never splits mid-sentence, except when a single sentence alone is
  bigger than the target** — at that point there's no non-mid-sentence
  option left, and the chunk runs long rather than truncate it. Sentence
  splitting is a regex boundary detector plus a small abbreviation guard
  list (`Rs.`, `Mr.`, `Ltd.`, ...) — not a statistical model.
- **`section`** is the nearest heading-shaped block at or before a chunk's
  start: short, no bullet marker, no sentence-terminal punctuation. This
  is exactly why `extract.py` joins blocks with a blank line rather than a
  single `\n` (above) — without that, a long bullet's own line-wrap could
  look like a short standalone heading. Still a text-only heuristic with a
  real ceiling: it has no way to tell a genuine section title apart from a
  pull-quote or graphic caption that happens to read the same way in plain
  text.
- **`id`** is `sha256(f"{document_id}|{page}|{offset}")` — deterministic
  by construction (same inputs, same id, forever) and specifically
  sensitive to `offset`, so a change to the accumulation logic that shifts
  where chunks start changes their ids too, rather than silently keeping
  stale ids pointing at different content. It's a plain sha256 hex string,
  not the `chunks.id uuid` column's shape — reconciling the two (e.g. as
  an idempotency key on insert) is the storage step's problem, not this
  module's.

### Tests

`tests/test_chunk.py` — sizing/overlap/id tests run against synthetic text
with a fixed, known token count per sentence (precise and fast, and
doesn't depend on any fixture's wording holding still): every chunk at or
under the target, non-final chunks landing close to it rather than
stopping early, a single oversized sentence staying whole in a one-chunk
result, consecutive chunks sharing a real trailing/leading run of whole
sentences worth at least 100 tokens, and `chunk_id` matching a hardcoded
expected hash (not just "equal to itself twice" — that alone wouldn't
catch a refactor that changes the hash *input format* but stays internally
consistent). Section detection is checked both on a clean synthetic
example and against the two real fixtures shared with `test_extract.py`.

## Layout

```
src/ingest/
  config.py            # env-backed Settings, loaded once via get_settings()
  cli.py                # `ingest` command group (click) — filings/transcripts (stubs), download
  db.py                 # Supabase client
  nse_fetch.py           # shared NSE session/probe/fetch — ported verbatim from nse-assist
  seeds.py                # the curated SEED_DOCUMENTS list, transcribed from ../SOURCES.md
  download.py              # downloads seeds.py, hashes, uploads to Storage, records in `documents`
  extract.py                # per-page PDF -> JSONL text extraction (PyMuPDF)
  chunk.py                    # page text -> overlapping, sentence-aware chunks
  providers/
    embeddings.py        # EmbeddingsProvider interface + CloudflareBgeEmbeddings (pinned)
    generation.py         # GenerationProvider interface + GeminiFlashGeneration (pinned)
scripts/
  probe_nse_access.py    # measures NSE access (PDF downloads, seed URLs) — see ../SOURCES.md
tests/
  test_extract.py         # fixture + unit tests for extract.py
  test_chunk.py             # sizing/overlap/section/id tests for chunk.py
  fixtures/                # two real single PDF pages — shared by both test files
```
