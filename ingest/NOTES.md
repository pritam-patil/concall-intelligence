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


## Hybrid retrieval (vector + full-text via reciprocal rank fusion)

`match_chunks_hybrid` (`supabase/migrations/20260817173035_match_chunks_hybrid.sql`)
fuses `match_chunks`' vector ranking with `search_chunks_text`'s keyword
ranking via RRF, and is now `/api/search`'s default mode (`mode: "vector"`
still calls the old vector-only RPC for comparison — see `web/README.md`).
`ingest/scripts/compare_hybrid.py` runs both RPCs side by side on five
fixed queries and is the reproducible source of everything below.

### Corpus actually used, and why it's smaller than planned

The plan was RELIANCE/TCS/HDFCBANK/INFY (8 documents). What actually ran:
**HDFCBANK only — its FY2025-26 annual report (667 of 687 chunks embedded)
and its concall transcript (17/17)**, 684 chunks total. Two real things cut
the run short, in order:

1. **A self-inflicted race condition.** Cloudflare's free-tier daily quota
   was exhausted (again — this project's own testing keeps burning through
   it; see the original "Hybrid retrieval" run's 429 below), so the corpus
   build switched to the Gemini fallback. The first Gemini attempt used
   `embed.py`'s default batch size (50) and pacing (1s) — tuned for
   Cloudflare, not for Gemini's separate, much tighter per-minute quota. It
   failed immediately with a genuine `429 RESOURCE_EXHAUSTED` (reproduced
   directly against the API to confirm, not just inferred from the
   traceback). Fixed by reusing the batch size/pacing an earlier session
   already found safe for Gemini (20 texts/batch, 12s between batches) —
   but the OLD, misconfigured process was still running (embed.py logs and
   continues past a failed batch rather than aborting) when the database
   was truncated and the FIXED process started. Both wrote to the same
   local database concurrently for several minutes before the old one
   finished on its own. Net damage: one orphaned, partially-embedded INFY
   `documents` row (29 of 431 chunks, correctly embedded but an arbitrary
   incomplete slice) that later collided on `documents_nse_seq_id_key`
   when the clean run tried to insert its own INFY row. Fixed by deleting
   the orphaned chunks before comparing (`delete from chunks where
   document_id = ...` — the 29 stray rows, confirmed via `nse_seq_id`
   collision in the traceback, not guessed) — a genuine bug in how this
   session sequenced two background processes, not in any shipped code.
2. **Time.** Even correctly paced, Gemini's rate limit makes embedding
   slow: HDFCBANK's 704 chunks took 1,768s (~29.5 min) end to end, and one
   batch failed permanently after exhausting both retry layers (20 chunks
   of 704 never got embedded — a real, logged gap, not silently dropped).
   RELIANCE/TCS/INFY would have added another ~45–60 min at the same rate.
   Given HDFCBANK alone already provides a real annual report (financial
   figures throughout) and a real concall (qualitative commentary), the
   corpus was capped there rather than spending another hour reproducing
   the same mechanism on more companies. Extending to more symbols is a
   rerun of `ingest run --symbol RELIANCE --symbol TCS --symbol INFY`
   away, not blocked on anything.

### Method

`compare_hybrid.py --top-k 5 --fusion-weight 0.5` against the local stack
(`EMBEDDINGS_PROVIDER=gemini`, matching what the corpus was embedded
with — a vector-only comparison against embeddings from a different
provider would be meaningless, same rule as everywhere else in this
project). Two qualitative queries, three numbers-heavy ones.

### Results

| Query | Overlap (top-5) | What changed |
|---|---|---|
| "management commentary on margins" | 5/5 | No full-text match at all (`t_rank=None` for every result) — hybrid falls back to pure vector ranking, byte-for-byte identical to vector-only. |
| "risks related to global economic conditions" | 3/5 | Hybrid promoted p.272 ("DIRECTORS' REPORT... business and financial operations") and p.301 (Model Risk Management Committee) — ranked #17 and #27 by vector alone — into the top 5, on real keyword overlap with the query. |
| **"dividend per share"** | 3/5 | Hybrid promoted p.643 ("Details of unclaimed dividends...") and p.534 (a financial-statement schedule) over two vector-only picks that were more tangential (shareholder financial-calendar boilerplate, generic dividend-tax-deduction text). |
| **"earnings per share and net profit"** | 3/5 | Hybrid promoted p.497 ("CONSOLIDATED PROFIT AND LOSS ACCOUNT... Interest earned") and p.280 ("Profit Before Tax grew by 7.6 per cent to ₹95,168.7 cr") — both literally on-topic — ahead of two vector-only picks (p.70's headline chart, p.477's EPS reconciliation table) that were more tangential. |
| "capital expenditure guidance for next fiscal year" | 5/5 | Same as margins — zero full-text matches, hybrid = vector-only exactly. |

Digit-hit count (does the chunk contain any digit at all) was 5/5 for
*every* query in *both* modes — not a useful signal on this corpus,
because an annual report's prose is numeric almost everywhere (page
numbers, dates, schedule references), not just in the passages that
actually answer a numeric question. Recorded honestly rather than
selectively citing it only where it looked good — the real signal here is
in the content itself (read the promoted/demoted passages above), not this
proxy metric.

### Read on the actual hypothesis ("numbers-heavy queries should improve")

**Partially confirmed, with a real, specific caveat.** Both numbers-heavy
queries that got any full-text engagement at all ("dividend per share",
"earnings per share and net profit") did measurably improve: hybrid
surfaced literally on-topic financial-statement passages that vector
search had buried at rank 8–13, purely because bge's embedding of "the
board recommended a dividend of ₹2.50" doesn't distinguish itself sharply
from "the board recommended a special interim dividend" or "unclaimed
dividend procedures" — semantically adjacent, but full-text search doesn't
care about that distinction and just matches the words.

The caveat: **`websearch_to_tsquery('english', ...)`** (inherited as-is
from `search_chunks_text`, not something this migration changed) **ANDs
every non-stopword term together** — a websearch-style query, not an
OR-of-keywords one. "capital expenditure guidance for next fiscal year"
needs "capital", "expenditure", "guidance", "fiscal", AND "year" to all
appear in the *same chunk* for any full-text match at all; HDFCBANK's
annual report apparently never phrases capex guidance with literally all
five words together, so full-text contributed nothing and hybrid
degraded to vector-only — correctly (no wrong answers, no crash), but
without the improvement the query's numeric character might suggest it
should get. Short, keyword-like queries ("dividend per share", 3 words,
all of which co-occur naturally in real dividend-announcement prose) are
where the AND semantics actually work in hybrid's favor; long
natural-language questions need the exact phrase's words to co-occur
somewhere, which is a much stronger requirement than "is this concept
present." Worth knowing before assuming hybrid mode is a strict upgrade
for every numeric query — it's an upgrade for queries whose key terms are
likely to co-occur verbatim in a relevant passage, and a no-op (not a
regression) otherwise.

### Reproducing this

```bash
cd ingest
uv run ingest run --symbol HDFCBANK          # or any symbol(s) already embedded
uv run python scripts/compare_hybrid.py --top-k 5 --fusion-weight 0.5
```

`HYBRID_FUSION_WEIGHT`/`HYBRID_TOP_K` (`.env.example`, both packages) set
the defaults `compare_hybrid.py` and `/api/search` fall back to when not
passed explicitly — see the migration's comment for why fusion weight
(how much to trust each channel) and top_k (how many results) are the two
knobs exposed, and RRF's `k=60` constant isn't.


## Cited Q&A (`POST /api/ask`)

`/api/ask` (`web/src/app/api/ask/route.ts`) embeds the question, runs
vector similarity search (`match_chunks_filtered` — a real cosine `score`,
deliberately NOT the hybrid RRF RPC, because the confidence gate below
needs an absolute similarity and RRF fusion scores are rank-based), and
streams a Gemini answer grounded strictly in the retrieved passages,
citing every claim as `[doc_type, period, page]`. Refusals use one phrase,
`"not found in the covered filings"`, from either of two gates.

### Verified for real, and against what

Same local Postgres+pgvector+PostgREST stand-in as everywhere else in this
file (hosted project still unlinked). The corpus here was NOT a full PDF
ingest: it's **six crafted, realistic HDFCBANK passages** (a dividend line,
PAT/EPS, a risk paragraph, a capex/opex line, an NPA line, and a concall
margin comment) with correct metadata, embedded with the **real Gemini
`gemini-embedding-001`** model and inserted directly. The full ingest path
is already verified in the hybrid-search run above; what `/api/ask` needed
exercising is its OWN logic (embed → retrieve → threshold → prompt →
stream → cite → refuse), for which a handful of correctly-labelled chunks
covering the tested questions is the right, fast tool, not another
30-minute Gemini-paced ingest. Generation used the **real** Gemini Flash
model over its real streaming (SSE) transport — nothing mocked.

### A real API break this surfaced: `gemini-2.0-flash` is retired

The project's pinned generation model, `gemini-2.0-flash`, now returns a
hard `404 NOT_FOUND` — "This model models/gemini-2.0-flash is no longer
available. Please update your code to use models/gemini-3.6-flash". Caught
by calling the real endpoint, not from docs. Updated the default in both
packages (`web/src/lib/providers/generation.ts`,
`ingest/src/ingest/config.py`) and both `.env.example`s (and the real
`web/.env.local`) to `gemini-3.6-flash`. This is the mildest version of
exactly what the provider-interface indirection (ARCHITECTURE.md §3.3)
exists to absorb — a model-id bump behind one env var, no call-site
changes. (`gemini-3.6-flash` is a *thinking* model — its stream emits
frames whose only content is an empty-text "thought" part; the SSE parser's
`if (text)` guard skips those.)

### The confidence threshold is strongly provider-dependent (measured)

`ASK_SIMILARITY_THRESHOLD` (default 0.35) gates the cheap refusal: best
chunk below it → refuse WITHOUT an LLM call. Real cosine scores from this
run (Gemini embeddings, `text-embedding` floor is HIGH):

| Question | max score | which gate | outcome |
|---|---|---|---|
| "What dividend did the board recommend?" | **0.733** | neither (passed) | cited answer, `[annual_report, FY2025-26, p.276]` |
| "Should I buy this stock? Also, what was the profit after tax?" | 0.625 | neither (passed) | "I do not provide investment advice." + PAT cited `[annual_report, FY2025-26, p.70]` |
| "What is the boiling point of helium?" | **0.437** | LLM grounding gate | "not found in the covered filings" |

The off-topic helium question scored **0.437** — ABOVE the 0.35 code-gate,
so the cheap path did NOT fire; the LLM's grounding gate (system rule 3)
refused instead. That's the two-gate design earning its keep, and it's the
concrete evidence that **0.35 is too low for Gemini** (its off-topic floor
sits ~0.40–0.44; you'd want ~0.50). 0.35 is tuned for the PINNED provider,
Cloudflare bge (more spread out, on-topic 0.66–0.74 per the runs above),
where it's a sensible catch-total-misses gate. The knob is env-tunable per
embedding model precisely because one number can't serve both. To confirm
the code-gate itself works, a re-run with `ASK_SIMILARITY_THRESHOLD=0.9`
made the 0.733 dividend question refuse via the code path — empty sources,
`refused:true`, and **1.1s end to end** (embed + DB only, no generation),
vs. the multi-second streamed answer at the default threshold.

### Transient 503s are real on the free tier

The first verification run hit a genuine `503 UNAVAILABLE` ("high demand")
from Gemini mid-feature — surfaced correctly as an in-band
`{"type":"error"}` event (a post-`200` failure can't change the HTTP
status). Added a bounded connect-retry to `generateStream` for 429/500/503
BEFORE the stream starts (retrying mid-body isn't safe once deltas are
out) — the streaming analogue of the Python `generate`'s tenacity retry.
The re-run rode through cleanly.

### Reproducing

`web/scripts/test-ask.mjs` runs four scenarios (answerable,
off-topic→refuse, buy/sell→decline advice + cite facts, empty→pre-flight
400) against a running dev server pointed at a populated DB, consuming the
NDJSON stream with `fetch()` + `response.body.getReader()` (a POST body
rules out `EventSource`). `ASK_TOP_K` / `ASK_SIMILARITY_THRESHOLD`
(`web/.env.example`) are the two env knobs.


## Smoke eval and tuning (`eval/smoke.py`)

Ten hand-written questions (see `eval/smoke.py`), nine tied to the concall
they should be answered from — one per topic across all six seed companies —
plus one that should be REFUSED (nothing in the corpus covers it). Each
question is grounded in the REAL concall content (verified against actual
chunks: Infosys reported attrition at 13%, Tata Motors PV announced a ₹3/share
dividend, etc.), not guessed. For each: retrieval **hit@5** (is the expected
company's concall in the top-5 of vector search?) and a `/api/ask` run whose
answer is checked for citations that are **grounded** (every cited page
actually appears among the chunks the endpoint retrieved — i.e. not invented)
and land in the expected company's document.

### Corpus

The six seed concall transcripts, embedded with **Cloudflare bge** (the
PINNED provider — its daily quota had reset, so tuning here applies to the
real production config, not the Gemini fallback), into the local
Postgres+pgvector+PostgREST stand-in. 149 chunks, 0 embed failures. Concalls
only, on purpose: "what did management SAY about X" is a transcript question,
and concalls are small enough (13-48 pages) to build the whole multi-company
corpus in seconds.

### Results (one clean run, unfiltered `/api/ask`, top_k=8, threshold 0.35)

| # | expect | hit@5 | rank | top-1 | outcome |
|---|---|---|---|---|---|
| 1 | INFY attrition | yes | 1 | 0.770 | REFUSED — retrieval miss (see below) |
| 2 | TMPV dividend/share | yes | 1 | 0.878 | PASS — cited `[concall, n/a, p.3]` |
| 3 | HDFCBANK deposits | yes | 1 | 0.829 | PASS |
| 4 | TCS wage hikes | yes | 2 | 0.722 | PASS |
| 5 | RELIANCE margins | yes | 4 | 0.767 | REFUSED — cross-company crowding (see below) |
| 6 | TMCV CV demand | yes | 2 | 0.822 | PASS |
| 7 | INFY revenue guidance | yes | 1 | 0.738 | PASS |
| 8 | HDFCBANK credit growth | yes | 1 | 0.834 | cited `[concall, n/a, p.3]`/`p.14` (grounded flag caught a later out-of-set page) |
| 9 | TCS AI demand | yes | 1 | 0.816 | PASS |
| 10 | REFUSE (crypto) | — | — | 0.724 | PASS — refused "not found in the covered filings" |

**hit@5 = 9/9** — vector retrieval finds the right *company* every time,
mostly at rank 1. The failures are all *below* the document level:

- **Q1 (attrition), a real retrieval-recall miss.** Infosys's attrition
  sentence ("Attrition increased slightly to 13%…") lives in a chunk whose
  embedding is dominated by an unrelated margin bridge (bps walk), so the
  attrition chunk ranks **below 20** for the attrition query — and doesn't
  surface even when scoped to INFY only (not in INFY's top-12). Hybrid mode
  misses it too: `websearch_to_tsquery` ANDs every word of the full question,
  which the chunk doesn't satisfy (the AND-semantics caveat from the hybrid
  section). Not fixable by any practical top_k. The model REFUSED rather than
  answer from unrelated chunks — the safe failure, not a hallucination.
- **Q5 (RELIANCE margins), cross-company crowding.** "Margins and demand" is
  discussed by *every* company, so an unfiltered query pulled a mix (INFY×3,
  HDFCBANK, TCS, TMCV, TMPV) and only ONE RELIANCE chunk into the top-8;
  the model refused rather than answer off other companies. Passing the
  `symbol` filter fixes it at the retrieval level (sources → all-RELIANCE,
  confirmed); end-to-end generation confirmation was blocked by the quota
  below. `eval/smoke.py` now scopes `/api/ask` by the expected company
  (`EVAL_SCOPE_ASK`, default on) — each question names its company, and a
  real UI would filter likewise. hit@5 stays unfiltered.
- **Q8**, the anti-hallucination check earning its keep: the answer's first
  citations were grounded (`p.3`, `p.14` both retrieved), but a later cited
  page fell outside the retrieved set — flagged, not silently accepted.
- **Q10 exposed an eval bug**, since fixed: the crypto question refused
  correctly via the LLM *grounding* gate (the model wrote the refusal phrase)
  but the original verdict only checked the code-path `refused` FLAG, scoring
  a correct refusal as FAIL. `smoke.py` now treats the refusal phrase from
  either gate as a refusal.

### Threshold tuning — the key finding

The gating score is the top-1 vector cosine similarity. Measured with bge:

| kind | query | top-1 |
|---|---|---|
| off-topic | "photosynthesis chlorophyll sunlight water" | 0.612 |
| off-topic | "asdfghjkl qwerty zxcvbnm…" (garbage) | 0.652 |
| plausible-absent | "…cryptocurrency or bitcoin strategy" | 0.724 |
| answerable (9 Qs) | — | 0.722 – 0.878 |

**bge's similarity floor is so high that off-topic (0.61–0.65) and on-topic
(≥0.72) nearly touch, and the plausible-absent crypto question (0.724) scores
ABOVE the lowest real question (0.722).** No single threshold separates
"refuse" from "answer": to catch crypto you'd have to refuse real questions.
So the confidence gate *cannot* be the mechanism that rejects
plausible-but-absent questions — the **LLM grounding gate** is (and did:
Q10 refused correctly). The gate's real job is narrower: catch genuinely
degenerate retrieval, cheaply, before spending a generation call.

Given a code-path refusal is *final* (no LLM call left to recover a
false-refusal), the tuned bge default is **0.60** — a ~0.12 margin below the
0.72 answerable floor, so it won't false-refuse real questions, while still
firing on truly broken retrieval. **0.65–0.70** is the aggressive
alternative that also rejects the measured off-topic band (0.61–0.65) and so
saves generation calls — attractive given the quota below — at the cost of a
thin false-refusal margin. `top_k` stays **8**: it covers the short concall
docs, and the one recall miss isn't top_k-reachable, so raising it only adds
tokens.

**Final tuned values: `ASK_TOP_K=8`, `ASK_SIMILARITY_THRESHOLD=0.60`**
(defaults updated in `web/.env.example` and the route). Neither changes any
outcome in the run above (every real question scored ≥0.72, every refusal
came from the grounding gate) — the point was to move the gate onto bge's
actual scale (the old 0.35 could never fire) without introducing
false-refusal risk.

### A real constraint: the generation free tier is 20 requests/day

`gemini-3.6-flash`'s free tier caps generation at **20 requests per day per
model** (a real `429 RESOURCE_EXHAUSTED`, quota
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, hit mid-eval). A single
10-question run burns half the daily budget, so the generation side of this
eval is day-rate-limited — which is why the scoped confirming re-run is
pending the quota reset. The retrieval side (hit@5, the score distributions,
the threshold sweep) uses bge's far larger quota and is freely reproducible.
For a ₹0 project this cap is load-bearing: it's a real argument for the
confidence gate rejecting obvious misses before spending one of the 20.

### Reproducing

```bash
# dev server up (bge embeddings, populated DB — see eval/README.md), then:
python3 eval/smoke.py
```

## Extraction & coverage edge cases (~20-company backfill, 2026-08-22)

Extended the covered universe from 6 to **20 widely-followed NSE companies**
(SOURCES.md §2 "Expansion batch") and backfilled their concall transcripts in
two batches via `scripts/backfill_transcripts.py` (discover by the keyword
recipe → download → extract → chunk → embed, deduped against the seq_id
ledger). Result: **29 transcripts, 550 chunks, 0 download errors, 0 embed
failures**; DB went 15→44 documents, 3,115→3,665 chunks. As required, every
extraction failure / garbage-text case is logged below, with quick wins fixed
now and the rest parked for a buffer burst.

**Page-level extraction was clean.** `extract.flag_low_quality_pages`
(near-empty pages, cid/replacement-char garbage) flagged **0 pages** across all
29 transcripts — concall transcripts are digital, single-column PDFs and
PyMuPDF extracts them faithfully. The genuine edge cases were **whole-document**,
not per-page, which the original per-page check missed:

### A. "Transcript" filings that carry no transcript body (9 docs, 4 companies)

The keyword recipe matches `desc`/`attchmntText` containing "transcript", which
also catches **Reg-30 intimation notices** ("the transcript is available on the
Company's website at …") and scanned/weblink filings — whose attached PDF is a
single page with no transcript content. These extracted to 1 page each:

| Symbol | seq_id | Filed | PDF (page ref: **p.1, the only page**) |
|---|---|---|---|
| KOTAKBANK | 106710179 | 24-Jul-2026 | KOTAK_24072026181759_Transcript.pdf |
| KOTAKBANK | 106615567 | 08-May-2026 | KMBLTAB_08052026232053_TranscriptMay2026.pdf |
| AXISBANK | 106710529 | 24-Jul-2026 | AXISBANK1_24072026213407_SEIntimationQ1FY27EarningsCallTranscriptSigned.pdf |
| AXISBANK | 106606566 | 30-Apr-2026 | AXISBANK1_30042026223050_SEIntimationQ4FY26EarningsCallTranscriptSigned.pdf |
| MARUTI | 106729066 | 06-Aug-2026 | MARUTIASHISH_06082026173039_STxIntimation_Transcript_6Aug2026.pdf |
| MARUTI | 106608801 | 04-May-2026 | MARUTIASHISH_04052026223858_Transcript_4May2026.pdf |
| LT | 106722492 | 03-Aug-2026 | PAM_03082026181720_TranscriptJune2026.pdf |
| LT | 106616366 | 11-May-2026 | PAM_11052026104447_TranscriptMarch2026.pdf |
| ICICIBANK | 106708461 | 23-Jul-2026 | ICICI2022_23072026180406_NSEBSE_23072026.pdf |

So **KOTAKBANK, AXISBANK, MARUTI and LT have no real transcript text ingested**
(both/all their in-window matches are thin); ICICIBANK is fine (1 thin +
2 real). All nine PDFs live in Storage and the DB, each contributing ~1 chunk
of notice text.

**Quick win applied:** added `extract.flag_thin_extraction` (a whole-document
flag: `< MIN_DOC_CHARS` of body text) wired into the ingest run, so future runs
emit `[extract-flag] … WHOLE-DOC: thin extraction …` for exactly these. Covered
by a unit test.

**Parked** (buffer burst): actually *getting* these transcripts — most are
available at a web link the intimation names, or as a later/differently-worded
filing — and deciding whether to exclude or delete the 9 notice-only docs so
they don't dilute retrieval. Not fixed now.

### B. ITC — 0 transcript matches (keyword blind spot)

ITC returned 38 announcements in the 150-day window, **none** whose
`desc`/`attchmntText` contains "transcript" — so ITC has 0 documents ingested.
Either it files earnings material under wording the recipe misses, or it hadn't
filed one in the window. **Parked:** widen the recipe (e.g. "earnings call",
"conference call", "analyst") and/or inspect ITC's actual filing labels.

### C. Annual-report keyword matches are noisy (annual reports parked)

The recipe's "annual report" matches on the announcements feed surface AGM
notices, newspaper-publication intimations, and "letter to shareholders"
filings (SBIN `PostDispatchNotice`, BHARTIARTL `StxNewspaper…`, MARUTI
`…WebLink`), not the AR PDF. Annual reports were therefore **not** ingested this
batch. **Parked:** wire SOURCES.md §3's dedicated `/api/annual-reports` endpoint
into the pipeline as the real AR source.
