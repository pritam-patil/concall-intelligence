# COSTS

What one question costs, how long it takes, and where the free tier runs out.

Everything below was **measured on 2026-08-29** against the real corpus (20
companies, 44 documents, 3,665 chunks) using
[`web/scripts/measure-costs.mjs`](web/scripts/measure-costs.mjs). Prices were
fetched from the providers' own pricing pages the same day. Numbers that were
*not* measured are called out as such — there are three of them, and they are
listed under [What these numbers are not](#what-these-numbers-are-not).

Re-run with:

```bash
cd web && npx next build && npx next start -p 3010 &
node scripts/measure-costs.mjs --base http://localhost:3010 --mode retrieval --rounds 3
node scripts/measure-costs.mjs --base http://localhost:3010 --mode ask --rounds 1
```

## Headline

| | |
|---|---|
| **Cost per question** | **$0.0124** (1.24 US cents) |
| **p95 end-to-end latency** | **18.8 s** (n=12; p50 10.7 s) |
| **First paid bottleneck at 100 questions/day** | **Gemini free tier — 20 requests/day/model.** Hit at question 21, five times under target. |
| Cost at 100 questions/day | $1.24/day ≈ **$37/month** (doubling to ~$75/month on 2027-01-01) |

The single biggest lever on both numbers is the generation model, not the
retrieval stack — see [The one change worth making](#the-one-change-worth-making).

## Tokens per question

Provider-reported, not estimated: `generateStream` now captures Gemini's
`usageMetadata` off the SSE frames and the route logs it on `ask.complete`
(and emits it on the `done` event). This matters because **83% of the billed
output is invisible** — thinking tokens appear in neither the prompt nor the
streamed answer, so no amount of counting characters on our side would find
them.

n = 12 questions, `gemini-3.5-flash`, context cap 6,000:

| | mean | p50 | p95 | max |
|---|---:|---:|---:|---:|
| Prompt (context + system + question) | 6,297 | 6,247 | 7,304 | 7,304 |
| Visible output | 350 | 356 | 868 | 868 |
| **Thinking** (billed at the output rate) | **1,702** | 1,595 | 2,693 | 2,693 |
| Billed output (visible + thinking) | 2,052 | — | — | — |
| Total | 8,349 | 8,342 | 9,808 | 9,808 |

Two things worth noticing:

- **Thinking dominates the expensive half.** Gemini's docs are explicit —
  *"when thinking is turned on, response pricing is the sum of output tokens
  and thinking tokens"* — so the 1,702-token mean is charged at $3.75/M, not
  the $0.75/M input rate. It is 5× the visible answer.
- **A refusal is not cheap.** Three of the twelve questions refused
  (`out=3`, i.e. the `NOT_FOUND` tag) and still burned 1,208–1,428 thinking
  tokens each. The similarity gate that refuses *before* generating saves the
  whole cost; the model-side grounding gate saves almost none of it.

### Where the prompt goes

Measured against the p50 prompt of 6,247 tokens:

| Part | Tokens | Share |
|---|---:|---|
| Retrieved context (after the cap) | ~5,230 | **84%** |
| System instruction (6 rules) | 609 | 10% |
| Question + prompt scaffolding | ~410 | 7% |

Retrieval returns ~870 estimated tokens per passage on this corpus, so a
default `top_k` of 8 asks for ~7,000 tokens before the cap trims it. Context
is the only part worth optimising; the system instruction is a rounding error
and shrinking it would cost grounding quality for ~10% of the input bill.

## What the context cap does

`ASK_MAX_CONTEXT_TOKENS` (default 6,000) drops whole passages from the tail
until the context fits. It is not a formality — **it engaged on 9 of the 12
sampled questions**, dropping 1–4 of 8 passages.

A/B on the same 6 questions and the same model (`gemini-3.1-flash-lite`),
capped vs. effectively uncapped (`ASK_MAX_CONTEXT_TOKENS=100000`):

| Question | Capped | Uncapped | Saved |
|---|---:|---:|---:|
| Infosys FY2025-26 guidance | 7,304 | 7,304 | — |
| Infosys large-deal momentum | 6,556 | 6,556 | — |
| HDFC Bank NIM | 6,911 | 9,270 | 25% |
| Deposits and advances | 6,806 | 8,853 | 23% |
| Reliance capex | 6,187 | 10,443 | 41% |
| O2C and retail segments | 6,247 | 12,743 | 51% |
| **Mean** | **6,668** | **9,195** | **27%** |

**No verdict changed.** The one refusal in the sample refused in both arms,
and the answered questions came back within ~10% of the same length. On this
evidence the cap costs ~27% of input tokens and buys nothing back in quality
loss — but six questions is a small sample, and recall was not otherwise
evaluated. Raise the budget toward 8,000 if answers start looking thin.

## Latency

**p95 end-to-end: 18.8 s** (n=12, p50 10.7 s, min 8.2 s). Small-n, so read it
as an order of magnitude, not a bound.

| Stage | p50 | Share |
|---|---:|---|
| Embed (warm cache) | **0 ms** | — |
| Embed (cold) | ~230 ms | 2% |
| Retrieve (2 RPCs, Supabase Tokyo) | 696 ms | 6% |
| Generate | 9,652 ms | **90%** |

Streaming hides some of this: the citation chips render at **1.1 s** (p50
time-to-`sources`, p95 1.7 s) — the user sees sources long before the first
answer token at 9.5 s (p95 15.7 s).

**Generation is ~90% of the wall clock.** No amount of retrieval tuning moves
the number materially.

### The query-embedding cache

Measured on the retrieval path, 12 questions × 3 rounds:

| | p50 | p95 |
|---|---:|---:|
| Round 1 (cold, all misses) | 923 ms | 2,552 ms |
| Rounds 2–3 (warm, all hits) | **696 ms** | **850 ms** |

A cache hit removes ~230 ms from p50 and ~1.7 s from p95 — the tail is where
it earns its keep, because a cold embedding call is the request's most
variable component. It saves essentially no money (see below); it buys
latency and one less dependency on a rate-limited third party per question.

Caveat: the cache is **per serverless instance**, so its hit rate depends on
Vercel keeping an instance warm. At a few questions a day it will mostly
miss. A shared cache (Redis, or a Postgres table keyed by the query hash) is
the upgrade if the hit rate ever needs to be predictable.

## Provider costs at free-tier limits

| Provider | What it does | Free tier | Headroom at 100 q/day |
|---|---|---|---|
| **Gemini** (`gemini-3.6-flash`) | Answer generation | **20 requests/day/model** (measured — the 429's own `quotaValue`) | ✗ **5× over at question 21** |
| Cloudflare Workers AI (`bge-base-en-v1.5`) | Query embedding | 10,000 neurons/day | ✓ 0.12 neurons/question → ~82,500 q/day |
| Cloudflare Workers AI (`llama-3.1-8b`) | Generation *fallback* | shares the same 10,000/day | ✗ 188 neurons/question → **exhausted at ~53 q/day** |
| Supabase | Postgres + pgvector + Storage | 500 MB DB, 5 GB egress/mo | ✓ corpus is ~4k chunks; ~105 MB/mo egress at 100 q/day |
| Vercel | Hosting + functions | Hobby (see caveat) | ✓ 3,000 invocations/month is not a plausible constraint |

Paid rates used, all fetched 2026-08-29:

- **Gemini 3.6 Flash** — $0.75/M input, $3.75/M output *through 2026-12-31*;
  **$1.50 / $7.50 from 2027-01-01**.
- **Workers AI** — $0.011 per 1,000 neurons; bge-base 6,058 neurons/M input
  tokens; llama-3.1-8b 25,608 in / 75,147 out per M.
- **Supabase Pro** — from $25/month.

### Cost per question, built up

| Component | Tokens | Rate | Cost |
|---|---:|---|---:|
| Input | 6,297 | $0.75/M | $0.004723 |
| Output incl. thinking | 2,052 | $3.75/M | $0.007693 |
| Query embedding | ~20 | 6,058 neurons/M | $0.0000013 |
| | | **Total** | **$0.01242** |

At 100 questions/day: **$1.24/day, ~$37/month**, rising to **~$75/month** when
Gemini's introductory rate ends on 2027-01-01. Embedding is 0.01% of the bill
— which is why the cache is a latency optimisation, not a cost one.

## The first paid bottleneck

**Gemini's free tier, at 20 generation requests per day per model.** Nothing
else is close:

- it binds at **20 q/day**, 5× under the 100 q/day target;
- the Cloudflare generation fallback binds next, at **~53 q/day** — so
  failing over does not rescue the target either, it just moves the wall;
- embeddings, Supabase and Vercel all have 2–3 orders of magnitude of
  headroom and are irrelevant at this scale.

Reaching 100 questions/day means paying Google roughly **$37/month**. Note
the free tier is *per model*, so a second model id is a second 20/day bucket —
that is a testing convenience, not a capacity plan.

There is also a **self-imposed** limit that binds first in practice:
`ASK_RATE_LIMIT_PER_DAY` defaults to 25 requests per IP per day
(`web/src/lib/guard.ts`), which exists precisely so a single caller cannot
drain the 20/day provider allowance.

## The one change worth making

`gemini-3.1-flash-lite`, measured on the same 6 prompts:

| | 3.5-flash (measured) | 3.1-flash-lite (measured) |
|---|---:|---:|
| Cost/question | $0.0124 | **$0.0019** |
| At 100 q/day | $37/month | **$5.81/month** |
| Mean latency | 11.4 s | **2.5 s** |
| Max latency | 18.8 s | **3.6 s** |
| Thinking tokens | 1,702 | **none reported** |

**~6.4× cheaper and ~4.6× faster**, because it does not spend 1,700 thinking
tokens per question. The trade is answer quality, which was **not** evaluated
here — the six answers were plausible and correctly cited, but nobody graded
them, and the smoke eval (`eval/smoke.py`) has not been run against this
model. That is the experiment to run before switching, and it is a cheap one.

## What these numbers are not

Three honest gaps:

1. **The configured model was never measured.** `GEMINI_MODEL` is
   `gemini-3.6-flash`; its free-tier quota (20/day) was exhausted before this
   exercise began, so the token and latency numbers come from
   `gemini-3.5-flash` as a stand-in. It is the closer sibling in behaviour but
   the **more expensive** one ($1.50/$9.00 vs $0.75/$3.75), so the *token*
   counts should transfer while the *cost* figures above — computed at 3.6
   Flash's rates — are the right ones to quote.
2. **n = 12.** A p95 from twelve samples is an order-of-magnitude statement.
   The retrieval-path percentiles (n = 36) are firmer than the end-to-end ones.
3. **Measured locally, not on Vercel.** A production `next start` on this Mac
   against the real (Tokyo) Supabase. Vercel's function cold starts and its
   hnd1 → Supabase path are not in these numbers, and the Vercel Hobby
   allowances could not be quoted — the Hobby column of
   `vercel.com/docs/limits` did not render in the fetch, so it is left blank
   above rather than guessed at.

Raw per-request samples for every run are written by `--out` and are what the
tables above were computed from.
