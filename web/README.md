This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

Needs `.env.local` (copy from `.env.example`) — Supabase URL/service-role
key, and Cloudflare account ID/API token for the embeddings provider. See
`../ARCHITECTURE.md` for the overall design.

## Pages

- `/` — landing page: value proposition, covered-companies grid with live
  per-company document counts and the latest ingestion date
  (`src/lib/coverage.ts`), three example questions, disclaimer.
- `/chat` — the Q&A surface. `/chat?q=<question>` auto-sends the question
  once on load; the landing page's examples and company tiles deep-link
  this way.
- `src/app/icon.svg` and `src/app/opengraph-image.tsx` are picked up by
  Next's file conventions for the favicon and `og:image`. Absolute OG URLs
  come from `metadataBase` in `layout.tsx` (Vercel's production URL, or
  `NEXT_PUBLIC_SITE_URL`).

## API routes

### `POST /api/search`

Hybrid search over chunks by default — embeds `query` server-side (the
same `EmbeddingsProvider` `ingest/` used to embed the chunks — see
`src/lib/providers/embeddings.ts`), then fuses vector similarity with
Postgres full-text search via reciprocal rank fusion (the
`match_chunks_hybrid` RPC — `supabase/migrations/`), and returns ranked
chunks with their source metadata. No generation step — that's `/api/ask`.

```jsonc
// Request
{
  "query": "management commentary on margins",
  "symbol": "TCS",           // optional
  "doc_type": "concall",     // optional: annual_report | concall | announcement
  "period": "FY2025-26",     // optional
  "top_k": 10,                // optional, default HYBRID_TOP_K env (else 10)
  "mode": "hybrid"            // optional: "hybrid" (default) | "vector" | "ask"
}
// Response
{ "query": "...", "mode": "hybrid", "results": [
  { "content": "...", "symbol": "TCS", "doc_type": "concall", "period": null,
    "page": 6, "source_url": "https://...", "score": 0.0164,
    "vector_rank": 3, "text_rank": 1 }
] }
```

`mode: "vector"` calls the earlier vector-only RPC (`match_chunks_filtered`)
instead, for comparison — `vector_rank`/`text_rank` are only present in
`hybrid`/`ask` mode, and are `null` when a result came from only one of the
two channels. `mode: "ask"` is exactly what `/api/ask` retrieves (see that
route below): hybrid over a keyword-shaped query, with the response also
carrying `keyword_query` and `max_score` (the top-1 cosine the gate reads).
The fusion balance between the two channels
(`HYBRID_FUSION_WEIGHT`) is an env-only default, not a request field —
see `.env.example` and `ingest/NOTES.md`'s "Hybrid retrieval" section for
why a numbers-heavy query like "dividend per share" is exactly the case
hybrid mode was built for (vector embeddings tend to blur exact figures
together; full-text search doesn't).

`node scripts/test-search.mjs` hits it with five sample queries (one
unfiltered, one symbol-filtered, one doc_type+period-filtered, and the
same numbers-heavy query run under both `mode`s back to back) against a
running dev server — see that file for exactly what it checks, and
`ingest/NOTES.md` for a real, populated dataset this has actually been
run against.

### `POST /api/ask`

Source-cited, grounded Q&A. Embeds the question, retrieves with
`src/lib/retrieval.ts` — the hybrid RPC (`match_chunks_hybrid`, vector +
full-text fused by RRF) over a **keyword-shaped** query (`src/lib/
keywords.ts`: the question's content terms OR'd together with a few finance
synonyms such as capex ↔ capital expenditure; a scoped question also drops
the company's own name words), plus a concurrent top-1 `match_chunks_filtered`
call whose cosine `score` is what the confidence gate reads (RRF scores are
rank-based and carry no "relevant enough" meaning) — and streams a Gemini
answer instructed to cite every claim with a numbered marker `[n]` (1-based
into `sources`) and to answer only from the retrieved passages.

Why the keyword shaping: `websearch_to_tsquery` ANDs every term, so a
natural-language question only keyword-matches a chunk containing *all* its
words — "What are Reliance's capital expenditure plans?" matched one
boilerplate paragraph while the annual report's capex figure and the
concall's capex-plan Q&A ranked #15–#78 by vector alone, and the model
(correctly) refused. With the shaped query those passages are the top
results. `POST /api/search` with `mode: "ask"` runs exactly this retrieval
and echoes the shaped `keyword_query`, for debugging and for `eval/smoke.py`.

```jsonc
// Request
{
  "question": "What dividend did the board recommend?",
  "symbol": "HDFCBANK",      // optional
  "doc_type": "annual_report", // optional: annual_report | concall | announcement
  "period": "FY2025-26",     // optional
  "top_k": 8                 // optional, default ASK_TOP_K env (else 8)
}
```

**Two refusal paths, one phrase** (`"not found in the covered filings"`):
if the best chunk's similarity is below `ASK_SIMILARITY_THRESHOLD` the
route refuses in code *without* an LLM call; if retrieval passed but the
passages still don't answer, the model refuses with the same phrase (system
rule 3). The model also never gives buy/sell/hold advice (system rule 4) —
it answers the citable facts and declines the advice part.

The model-side refusal is **normalized in code**, because the smaller
Cloudflare fallback model (which serves once Gemini's 20/day free-tier
quota is spent) doesn't reliably stick to the phrase: it paraphrases
("Management did not discuss X in the provided passages") and then rambles
about adjacent passages with citations. The route holds back the opening
sentence, and if it reads as a refusal emits exactly the phrase and drops
the rest; a reply that ends with no `[n]` citation at all is likewise
reported as refused on the `done` event (rule 2 makes every real answer
carry one). The same buffer strips the fallback model's habit of echoing
the question before answering. `eval/smoke.py`'s REFUSE control is what
caught all three behaviours.

**Response is a stream**, `Content-Type: application/x-ndjson` — one JSON
object per line. The cited chunks arrive first so the UI can render
citations before the answer text streams in:

```jsonc
{"type":"sources","sources":[ /* MatchedChunk[] */ ],"max_score":0.62,"threshold":0.6}
{"type":"delta","text":"The board recommended "}
{"type":"delta","text":"a special interim dividend of ₹2.50 [3]."}
{"type":"done","refused":false}
```

On a threshold refusal, `sources` is `[]` (an empty list is consistent
with "not found") but `max_score` is still reported so you can see *why* it
refused. A generation failure after streaming has begun can't change the
already-sent `200`, so it surfaces in-band as
`{"type":"error","error":"..."}`; failures *before* streaming (bad input,
embedding, the DB query) return a normal JSON error with a real status
code, like `/api/search`. Consume it with `fetch()` +
`response.body.getReader()` (a POST body rules out `EventSource`) — see
`scripts/test-ask.mjs` for a worked reader.

`node scripts/test-ask.mjs` hits it with four scenarios (answerable,
off-topic threshold-refusal, a buy/sell question, and an empty-question
pre-flight 400) against a running dev server.

Local testing tip: the per-IP daily cap keys on `x-forwarded-for`, and with
no proxy in front of a local server every request lands in one shared
`unknown` bucket — which test scripts exhaust quickly. Send
`-H 'x-forwarded-for: 203.0.113.7'` (any address) to use a fresh bucket.
On Vercel the platform sets that header itself, so this isn't a production
bypass.

### `GET /api/health`

"Can this deployment answer a question right now?" — the uptime gate and the
drift guard in one. Probes run concurrently, each time-boxed by
`HEALTH_PROBE_TIMEOUT_MS` (default 5s), and **spend no quota**: the database
check is three `count: exact` reads, and each provider check is a metadata
call (`models.get` on Gemini, the models catalogue on Workers AI) that
validates network + credentials without an embedding or a generation. Gemini's
also confirms the model id still exists (a retired name — the
`gemini-2.0-flash` incident — shows up here); Workers AI's can't, because
Cloudflare's catalogue doesn't list every servable id (measured: the
`llama-3.1-8b-instruct` fallback runs but isn't catalogued). Safe to poll
every minute.

```jsonc
{
  "status": "ok",                       // ok | degraded | error
  "problems": [],                       // one line per reason when not ok
  "checked_at": "2026-08-23T…Z",
  "latency_ms": 412,
  "deployment": { "env": "production", "commit": "b30cba1", "region": "hnd1" },
  "db": {
    "project_ref": "gfewmnvycrdhensfveqf",   // WHICH Supabase project — drift shows here
    "ok": true, "latency_ms": 180, "error": null,
    "counts": { "companies": 20, "documents": 61, "chunks": 8123 }
    // "errors": { "chunks": "…PGRST205…" } when a table can't be read
  },
  "providers": {
    "embeddings":          { "provider": "cloudflare_bge", "model": "@cf/baai/bge-base-en-v1.5", "ok": true, "latency_ms": 130, "error": null },
    "generation":          { "provider": "gemini_flash",   "model": "gemini-3.6-flash",          "ok": true, "latency_ms": 210, "error": null },
    "generation_fallback": { "provider": "cloudflare",     "model": "@cf/meta/llama-3.1-8b-instruct", "ok": true, "latency_ms": 125, "error": null }
  }
}
```

HTTP `200` only for `ok`; `503` otherwise, so a monitor can alert on the
status code alone and read `problems` for the why:

| status     | meaning                                                                                                   |
| ---------- | --------------------------------------------------------------------------------------------------------- |
| `ok`       | DB readable with companies seeded; embeddings, primary and fallback generation all reachable.             |
| `degraded` | Still answering, but not at full strength — primary generation down (answers come from the failover), the failover down, or `companies` empty. |
| `error`    | Cannot answer — a table is unreadable / schema not pushed, DB env missing, embeddings down, or every generation provider down. |

## Production logging

Structured JSON lines, one per event, via `src/lib/log.ts` — no SDK or vendor
account; Vercel's Runtime Logs capture stdout/stderr per invocation and parse
a JSON line into filterable fields. `LOG_LEVEL` (default `info`) sets the
floor. Every line carries `event`, and request-scoped lines carry
`request_id` (Vercel's `x-vercel-id`, so a line can be matched to the
platform's own invocation record), the route, a truncated `question`, an
`ip_hash` (never the raw IP), and per-stage timings.

| event                                        | when                                                                    |
| -------------------------------------------- | ----------------------------------------------------------------------- |
| `ask.complete`                               | every `/api/ask` that reached the stream — `outcome` is `answered`, `refused_threshold`, `refused_model`, `extractive` or `interrupted`; carries `embed_ms` / `retrieve_ms` / `generate_ms` / `total_ms`, `max_score`, `sources`, `answer_chars` |
| `ask.rejected`                               | a guardrail stopped it before any provider spend (`too_long`, `injection`, `rate_limited`) |
| `ask.embed_failed` / `ask.retrieval_failed`  | pre-stream failures (these return a real HTTP error)                     |
| `ask.model_refusal` / `ask.untagged_reply`   | the model said NOT_FOUND / skipped the verdict tag (with the opening text) |
| `ask.generation_failed` / `ask.generation_interrupted` | every provider failed (extractive fallback served) / the stream died mid-answer |
| `generation.failover`                        | primary generation failed before any output; the fallback took over      |
| `search.complete` / `search.rejected` / `search.*_failed` | the `/api/search` equivalents                               |
| `guard.rate_limit_unavailable`               | the limiter RPC failed and the request was allowed through (fail-open)   |
| `health.not_ok`                              | `/api/health` returned degraded/error                                    |
| `request.error`                              | any server error Next.js itself caught (`onRequestError` in `src/instrumentation.ts`) — route, digest, stack |

In the Vercel dashboard: Project → **Logs**, then filter on e.g.
`event:ask.complete` or a `request_id`. If a hosted error tracker is ever
wanted, `onRequestError` is the one place to forward from.

## Deploying to Vercel

The Vercel project (`concall-intelligence`) is **connected to this GitHub
repo** with **Root Directory = `web`** and the Next.js preset (this is a
monorepo; `ingest/` runs on GitHub Actions, not Vercel). So:

- **Push to `main` → production** (https://concall-intelligence.vercel.app).
- **Any other branch / PR → a preview deployment**, linked from the PR by
  the Vercel GitHub app.
- Commits that don't touch `web/` are skipped (the project's Ignored Build
  Step runs `git diff --quiet HEAD^ HEAD -- <repo>/web`), so the nightly
  ingest ledger commits on `main` don't trigger rebuilds.
- Env vars live in the project (Settings → Environment Variables); a change
  needs a redeploy to take effect.

The CLI is optional — for pushing env vars, or deploying a working tree
without committing. Because Root Directory is set, **run it from the repo
root** (the link lives in `<repo>/.vercel/`, gitignored; `.vercelignore`
keeps `ingest/`, `data/` etc. out of the upload):

```bash
vercel login                                   # once per machine
vercel link --project concall-intelligence     # once per checkout, at the repo root
web/scripts/vercel-env-push.sh                 # web/.env.local -> Production env vars (values never echoed)
vercel                                         # preview deployment of the working tree
vercel --prod                                  # production deployment of the working tree
curl -s https://concall-intelligence.vercel.app/api/health | jq .status   # expect "ok"
```

What's pinned in the repo, and why:

- **Region `hnd1` (Tokyo)** in `vercel.json`. The canonical Supabase
  project lives in AWS `ap-northeast-1` (resolved from the database host's
  IPv6 address against AWS's published ranges — it is *not* in Mumbai).
  Each question makes three to four sequential Supabase calls, so the
  functions sit next to the database rather than in Vercel's default
  `iad1`. Static assets still come from the edge CDN everywhere.
- **`maxDuration`** per route: `/api/ask` 60s (embed + retrieve + a streamed
  generation with connect retries and a failover), `/api/search` 30s,
  `/api/health` 15s. All within the Hobby plan's ceiling.
- **Env vars**: everything in `.env.example` except `NEXT_PUBLIC_SITE_URL`
  (Vercel supplies `VERCEL_PROJECT_PRODUCTION_URL`, which `layout.tsx` uses
  for absolute OG URLs). `scripts/vercel-env-push.sh` pushes them all from
  `.env.local`; re-run it after rotating a key, then redeploy. Names that
  look like secrets (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`) are
  stored as Vercel "sensitive" variables (write-only); plain config stays
  readable so `vercel env pull` can rebuild a `.env.local`.
- **Database connections**: `web/` never opens a Postgres connection —
  supabase-js talks to PostgREST over HTTPS and PostgREST owns a fixed
  server-side pool — so serverless fan-out can't exhaust the free tier's
  connection ceiling. `src/lib/supabase.ts` keeps one memoised client per
  function instance (reusing the keep-alive HTTP pool across warm
  invocations) and bounds every call with `SUPABASE_REQUEST_TIMEOUT_MS`.
  Don't add a `postgres://` driver to `web/`; if one is ever unavoidable,
  use the Supavisor transaction pooler (port 6543), never port 5432.
- **Monitoring**: point any uptime monitor at `/api/health` and alert on a
  non-200.
