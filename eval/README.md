# eval

Smoke evals for the retrieval + cited-Q&A stack. Black-box: they talk only
to a running dev server, the same way any client would.

## `smoke.py`

Ten hand-written questions, each tied to the concall it should be answered
from (nine answerable across all six seed companies + one that should be
REFUSED — nothing in the corpus covers it). For each question it:

1. **Retrieval / hit@5** — `POST /api/search` in `mode:"vector"` (the
   retrieval `/api/ask` actually feeds on) and checks whether the expected
   company's concall is in the top 5.
2. **Generation / citations** — `POST /api/ask`, consumes the NDJSON
   stream, and checks the answer carries `[doc_type, period, page]`
   citations whose pages are **grounded** (they appear among the chunks the
   endpoint actually retrieved — i.e. not invented) and that at least one
   citation lands in the **expected** company's document.

It prints a results table, a hit@5 / verdict summary, and an offline
**threshold sweep** — for candidate `ASK_SIMILARITY_THRESHOLD` values, how
many answerable questions would be wrongly refused vs. whether the REFUSE
question is caught by the gate. See `ingest/NOTES.md` ("Smoke eval and
tuning") for the tuned `ASK_TOP_K` / `ASK_SIMILARITY_THRESHOLD` this
produced.

The questions are grounded in the **real** seed concalls — each was written
against actual chunk content (e.g. Infosys's transcript really does report
attrition at 13%, Tata Motors PV really did announce a ₹3/share dividend),
not guessed.

### Prerequisites

- A running dev server (`web/`) whose env points at a **populated**
  database — the six seed concalls embedded with the configured
  `EMBEDDINGS_PROVIDER`. The hosted Supabase schema isn't pushed yet, so in
  practice this means the local Postgres+pgvector+PostgREST stand-in (see
  `.claude/skills/local-supabase-stack`) plus a concall ingest run.
- `GEMINI_API_KEY` set for the dev server (generation runs the real model).

### Run

```bash
# with the dev server up on :3000 and the DB populated
python3 eval/smoke.py

# point at other hosts / tune the ask top_k per request
SEARCH_API_URL=http://localhost:3000/api/search \
ASK_API_URL=http://localhost:3000/api/ask \
EVAL_ASK_TOP_K=8 \
python3 eval/smoke.py
```

Stdlib only (`urllib`) — no dependencies, no venv. Exit code is non-zero if
any question's verdict is not PASS.
