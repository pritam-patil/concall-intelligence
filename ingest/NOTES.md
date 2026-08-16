# NOTES

Real output from a real, full `ingest run` across all six pilot symbols —
RELIANCE, TCS, HDFCBANK, INFY, and Tata Motors' post-demerger successors
TMCV/TMPV (TATAMOTORS itself no longer exists — see SOURCES.md §2). "Run it
for the 5 seeded companies" — six rows for five slots, same reason as
everywhere else this has come up in this project.

## What actually ran, and against what

Real NSE downloads (all 12 documents in `ingest/seeds.py`), real PyMuPDF
extraction, real chunking, real Cloudflare bge embeddings — none of that is
simulated. What's a stand-in: **the database**. The real hosted Supabase
project still doesn't have the `documents`/`chunks` schema pushed (needs a
Management API access token + DB password this environment doesn't have —
see `ingest/README.md`'s "Connecting to Supabase" section, unchanged since
first flagged there). So this run wrote to a local Postgres 17 + pgvector
instance, both migrations applied, fronted by a standalone PostgREST — the
same verification method used for `download.py` and `embed.py`, extended to
a real production-scale run instead of a handful of test chunks.

Storage is NOT a stand-in: PDF uploads went to the actual hosted project's
`filings` bucket, which now genuinely holds all 12 documents (up to ~21MB
each, real files, not test pollution — see `ingest/README.md`'s "Embedding"
section for the one file that was already there from before this run, and
below for a Storage duplicate-object bug that discovery surfaced).

```
ingest run --symbol RELIANCE --symbol TCS --symbol HDFCBANK \
           --symbol INFY --symbol TMCV --symbol TMPV
```

**Result: 12/12 documents, 2,833 pages, 3,039 chunks, 3,039 embedded, 0
download errors, 0 embed failures.** Wall-clock: downloads + extraction +
chunking a few minutes (annual reports run 300–700 pages each), embedding
itself 20–25s per symbol (Cloudflare batches of 50, ~1s pacing between
batches — see `ingest/README.md`'s "Embedding" section).

## A real bug this run surfaced

`download.py`'s Storage upload crashed (`409 Duplicate`) on RELIANCE's
transcript — because that exact file was already in the real bucket from
`embed.py`'s own verification run two commits ago, but the (fresh) local
database didn't know that, so the dedup check that's supposed to prevent a
redundant upload never fired. Fixed by adding `x-upsert: true` to the
upload call: the dedup checks already rule out a `documents` **row** for
this content before the upload is even attempted, so a Storage **object**
already sitting at the same path is safe to overwrite rather than error on
— it's derived from `(symbol, doc_type, filename)`, so a collision there
already means "the same file," and this call is about to write those exact
bytes anyway. Re-ran clean after the fix.

## 1. Chunk counts per document

| Symbol | Doc type | Period | Chunks |
|---|---|---|---|
| HDFCBANK | annual_report | FY2025-26 | 687 |
| HDFCBANK | concall | — | 17 |
| INFY | annual_report | FY2025-26 | 431 |
| INFY | concall | — | 48 |
| RELIANCE | annual_report | FY2025-26 | 308 |
| RELIANCE | concall | — | 32 |
| TCS | annual_report | FY2025-26 | 367 |
| TCS | concall | — | 25 |
| TMCV | annual_report | FY2025-26 | 496 |
| TMCV | concall | — | 13 |
| TMPV | annual_report | FY2025-26 | 601 |
| TMPV | concall | — | 14 |

**Total: 3,039 chunks across 12 documents.** Concall `period` is blank —
`chunk.py`'s design deliberately leaves it null for concalls rather than
guess a quarter label from unstructured text (see `ingest/README.md`'s
"Chunking text" section); annual reports get a real one straight from
NSE's own `fromYr`/`toYr` fields.

Annual-report chunk counts track page count closely but not 1:1 (a
687-chunk HDFCBANK annual report was 678 pages) — most pages produce one
chunk, some dense ones produce two, matching what `chunk.py`'s own tests
already established about ~800-token packing.

## 2. Average tokens per chunk

Overall: **453.8 tokens/chunk** across all 3,039 chunks — meaningfully
under the ~800 target, which is expected and not a bug: `chunk.py` accumulates
whole *sentences* up to the target rather than padding to it, and most
individual pages (the unit chunking never crosses — see `chunk.py`'s
module docstring) don't contain 800 tokens' worth of prose in the first
place, especially concall pages, which are often mostly white space and a
speaker label.

| Symbol | Doc type | Avg tokens | Min | Max |
|---|---|---|---|---|
| HDFCBANK | annual_report | 430.1 | 1 | 855 |
| HDFCBANK | concall | 564.3 | 48 | 707 |
| INFY | annual_report | 451.4 | 1 | 807 |
| INFY | concall | 363.4 | 3 | 527 |
| RELIANCE | annual_report | 601.4 | 7 | 1,810 |
| RELIANCE | concall | 533.3 | 124 | 710 |
| TCS | annual_report | 432.6 | 11 | 800 |
| TCS | concall | 298.4 | 188 | 364 |
| TMCV | annual_report | 424.7 | 3 | 800 |
| TMCV | concall | 520.2 | 187 | 667 |
| TMPV | annual_report | 445.5 | 8 | 1,516 |
| TMPV | concall | 596.9 | 185 | 787 |

Two things worth flagging, not fixing — both expected given how `chunk.py`
and `extract.py` already work, not new findings:
- **`min_tokens: 1`** (HDFCBANK, INFY annual reports) — a near-empty page
  (a section divider, a mostly-blank transition page) still produces one
  chunk. Correct: `extract_pdf` returns a row per page regardless of how
  little survives header/footer stripping, and `chunk_page` returns `[]`
  only for genuinely blank text, not "very short."
- **`max_tokens: 1,810`** (RELIANCE annual report) — above the ~800
  target. This is `chunk.py`'s documented, deliberate tradeoff: a single
  sentence bigger than the target is kept whole rather than cut mid-
  sentence (see `test_single_oversized_sentence_is_not_split` in
  `tests/test_chunk.py`) — a real page with one very long run-on
  sentence (or a sentence-boundary false negative from a financial-text
  abbreviation `chunk.py`'s guard list doesn't cover) produces exactly
  this.

## 3. Similarity query: "management commentary on margins"

Query embedded with the same provider (`cloudflare_bge`) the chunks were
embedded with — see `ingest/sanity.sql` for the exact reproduction
command. Top 5 by cosine similarity via `match_chunks`:

| Rank | Symbol | Page | Section | Similarity | Content preview |
|---|---|---|---|---|---|
| 1 | TCS | 23 | — | 0.7415 | "...some part of the gains into things which will help us achieve our aspiration or long-term commitments. At times, we have seen a lot of apprehension from investors given our industry-leading proﬁt..." |
| 2 | TCS | 6 | — | 0.7246 | "As we have demonstrated in the past, our approach is to not optimize margins in isolation, but to invest in capabilities that strengthen our long-term competitiveness while continuing to deliver in..." |
| 3 | INFY | 14 | — | 0.7196 | "If you look at the last three-year period, we have been consistently able to hold or improve our margins despite investment in business, whether it is AI, whether it is talent or whether it is sales..." |
| 4 | HDFCBANK | 10 | HDFC Bank Limited / July 18, 2026 | 0.7179 | "HDFC Bank Limited ... July 18, 2026 ... what we want. We need to ensure that we are there for all the needs of the corporate customer, whether it is for deposits, whether it's for cash management, whet..." |
| 5 | INFY | 13 | — | 0.7165 | "And the last question is, what would you classify at least in your tenure as the most challenging period? Was it the COVID or is it the AI-led transformation for Infosys? And Jayesh, one question on..." |

**Read on quality:** ranks 1–3 are genuinely, precisely on-topic —
TCS's two hits are management directly addressing margin strategy
("not optimize margins in isolation, but invest in..."), and INFY's is
management stating margins held or improved over three years despite
AI/talent/sales investment. That's real semantic retrieval working, not
keyword luck — none of those three chunks contain the literal query
string. Ranks 4–5 are real concall content but only loosely on-topic
(HDFCBANK's hit is a segment intro, not margin commentary; INFY's is an
unrelated closing question) — a reasonable place for relevance to taper
off at 5 results from a 3,039-chunk corpus, and the similarity scores
(0.718–0.717) are visibly lower than ranks 1–3 (0.742–0.720), so the
ranking itself is behaving sensibly even where the content match is
weaker.

`section` is blank for 4 of 5 — expected: these are concall pages, and
`chunk.py`'s heading heuristic is text-position-based (see `chunk.py`'s
module docstring); a Q&A transcript's speaker-label lines don't look like
section headings the way an annual report's subheadings do. HDFCBANK's
hit did get a `section` — "HDFC Bank Limited" / "July 18, 2026" (a
letterhead-style block that read as heading-shaped) — which is exactly
the kind of imprecision `chunk.py`'s docstring already warns section
detection has: "no way to tell a genuine section title apart from a
pull-quote or graphic caption that happens to read the same way in plain
text."

## Reproducing this

```bash
cd ingest
uv run ingest run --symbol RELIANCE --symbol TCS --symbol HDFCBANK \
                   --symbol INFY --symbol TMCV --symbol TMPV
# then, against whichever Postgres the run actually wrote to:
psql "$DATABASE_URL" -v query_embedding="$(python3 -c "
from ingest.config import get_settings
from ingest.providers.embeddings import get_embeddings_provider
provider = get_embeddings_provider(get_settings())
vec = provider.embed(['management commentary on margins'])[0]
print('[' + ','.join(str(x) for x in vec) + ']')
")" -f sanity.sql
```

Against the real hosted project, this is unchanged once the schema's
pushed (`ingest/README.md`, "Connecting to Supabase") — nothing about
`run.py`/`sanity.sql` is local-stack-specific; they use the same
`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` config path as everything else
in this package.
