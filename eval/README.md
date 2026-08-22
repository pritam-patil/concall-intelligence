# eval

Smoke evals for the retrieval + cited-Q&A stack. Black-box: they talk only
to a running dev server, the same way any client would.

## `smoke.py`

Eleven hand-written questions, each tied to the filing it should be
answered from (ten answerable across the six seed companies + one that
should be REFUSED — nothing in the corpus covers it). For each question it:

1. **Retrieval / hit@5** — `POST /api/search` in `mode:"ask"` (exactly the
   retrieval `/api/ask` feeds on: hybrid vector + keyword-shaped full-text,
   see `web/src/lib/retrieval.ts`) and checks whether the expected company
   is in the top 5. The response's `max_score` — the top-1 cosine the
   confidence gate reads — feeds the threshold sweep.
2. **Generation / citations** — `POST /api/ask`, consumes the NDJSON
   stream, and checks the answer carries numbered `[n]` citations (1-based
   into the `sources` event, as the UI renders them) that are **grounded**
   (every `n` indexes a chunk the endpoint actually retrieved — i.e. not
   invented) and that at least one citation lands in the **expected**
   company's document.

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

### Which generation provider answered matters

The answerable questions pass on either provider, but the REFUSE control is
only reliable on the primary model. Once Gemini's free tier (20 requests/
day) is spent, `/api/ask` fails over to the Cloudflare fallback
(`llama-3.1-8b-instruct`), which — even with the ANSWER:/NOT_FOUND verdict
protocol in the prompt — will, roughly one run in three, tag a decorated
refusal as an answer and cite an adjacent passage ("did not discuss
cryptocurrency… however, management mentioned token cost [1]"). That is a
FAIL by this eval's rules, and correctly so: it is the fallback model's
judgment, not a retrieval or parsing bug. The dev server log says which
provider served each request (`[generation] … failing over`), so read a
REFUSE-control FAIL together with that. Run the eval early in the (Pacific)
day if you want a clean primary-model run.

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
