/**
 * Measures what a question COSTS and how long it takes, for COSTS.md.
 *
 *     node scripts/measure-costs.mjs --mode retrieval --rounds 2
 *     node scripts/measure-costs.mjs --mode ask --rounds 1 --limit 12
 *
 * Two modes, because the two halves of a question have very different
 * budgets and very different blast radii:
 *
 *   --mode retrieval   POST /api/search {mode:"ask"} — embed + hybrid
 *                      retrieval, NO generation. Cheap enough to sample
 *                      properly, so this is where the percentiles come from.
 *   --mode ask         POST /api/ask — the whole path including a streamed
 *                      answer, and the only way to get the provider's own
 *                      token accounting (the `usage` on the `done` event).
 *                      Sample size is capped by the generation free tier
 *                      (20 requests/day/model), so treat its percentiles as
 *                      indicative, not tight.
 *
 * `--rounds 2` replays the same questions a second time, which is how the
 * query-embedding cache is measured: round 1 is all misses, round 2 all hits
 * against a warm instance, and the difference is the embedding call's real
 * contribution to latency.
 *
 * Percentiles are reported with their sample size because at these n a p95 is
 * an order-of-magnitude statement, not a tight bound. Writes raw per-request
 * samples to --out so the numbers in COSTS.md can be re-derived rather than
 * taken on trust.
 */

import fs from "node:fs";

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i === -1 ? fallback : args[i + 1];
};

const BASE = opt("base", "http://localhost:3000");
const MODE = opt("mode", "retrieval");
const ROUNDS = Number(opt("rounds", 2));
const LIMIT = Number(opt("limit", 0));
const OUT = opt("out", null);
const PAUSE_MS = Number(opt("pause", 250));

// Real questions from the app's own starter set (src/lib/suggestions.ts) plus
// the scoped variants a user actually sends — not synthetic strings, so the
// retrieved context sizes are representative of production traffic.
const QUESTIONS = [
  { q: "What did Infosys say about its FY2025-26 revenue growth guidance?", symbol: "INFY" },
  { q: "How did Infosys describe large-deal momentum?", symbol: "INFY" },
  { q: "How did HDFC Bank management describe net interest margin last quarter?", symbol: "HDFCBANK" },
  { q: "How did deposits and advances grow last quarter?", symbol: "HDFCBANK" },
  { q: "What did Reliance say about its capex plans and spending?", symbol: "RELIANCE" },
  { q: "What did management say about the O2C and retail segments?", symbol: "RELIANCE" },
  { q: "What did TCS management say about deal wins (TCV) and margins?", symbol: "TCS" },
  { q: "How did TCS describe demand across BFSI and other verticals?", symbol: "TCS" },
  { q: "What did management say about commercial vehicle demand?", symbol: "TMCV" },
  { q: "What did management say about passenger vehicle and EV demand?", symbol: "TMPV" },
  { q: "What dividend did the board recommend for FY2025-26?", symbol: null },
  { q: "What are the key risks noted in the annual report?", symbol: null },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function percentile(values, p) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  // Nearest-rank: the smallest value at or above the pth percentile. At the
  // sample sizes here, interpolating between neighbours would imply a
  // precision the data does not have.
  const rank = Math.ceil((p / 100) * sorted.length);
  return sorted[Math.max(0, rank - 1)];
}

const summarize = (values) => ({
  n: values.length,
  min: values.length ? Math.min(...values) : null,
  p50: percentile(values, 50),
  p95: percentile(values, 95),
  max: values.length ? Math.max(...values) : null,
  mean: values.length ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : null,
});

async function measureRetrieval({ q, symbol }) {
  const started = performance.now();
  const res = await fetch(`${BASE}/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: q, symbol: symbol ?? undefined, mode: "ask" }),
  });
  const body = await res.json();
  const total_ms = Math.round(performance.now() - started);
  if (!res.ok) return { ok: false, status: res.status, error: body?.error, total_ms };
  const chunks = body.results ?? [];
  return {
    ok: true,
    total_ms,
    sources: chunks.length,
    // What the model WOULD have been sent, by the same estimator the route
    // caps on — so retrieval-only runs still report a context size.
    context_chars: chunks.reduce((sum, c) => sum + c.content.length, 0),
  };
}

async function measureAsk({ q, symbol }) {
  const started = performance.now();
  const res = await fetch(`${BASE}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: q, symbol: symbol ?? undefined }),
  });
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    return { ok: false, status: res.status, error: body?.error, total_ms: Math.round(performance.now() - started) };
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sources_ms = null;
  let first_delta_ms = null;
  let answer = "";
  let sources = 0;
  let done = null;

  for (;;) {
    const { done: finished, value } = await reader.read();
    if (finished) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.type === "sources") {
        sources_ms = Math.round(performance.now() - started);
        sources = ev.sources.length;
      } else if (ev.type === "delta") {
        if (first_delta_ms === null) first_delta_ms = Math.round(performance.now() - started);
        answer += ev.text;
      } else if (ev.type === "done") {
        done = ev;
      } else if (ev.type === "error") {
        return { ok: false, error: ev.error, total_ms: Math.round(performance.now() - started) };
      }
    }
  }
  return {
    ok: true,
    total_ms: Math.round(performance.now() - started),
    sources_ms,
    first_delta_ms,
    sources,
    refused: done?.refused ?? null,
    usage: done?.usage ?? null,
    answer_chars: answer.length,
  };
}

const questions = LIMIT ? QUESTIONS.slice(0, LIMIT) : QUESTIONS;
const measure = MODE === "ask" ? measureAsk : measureRetrieval;
const samples = [];

console.log(`\nmode=${MODE}  base=${BASE}  questions=${questions.length}  rounds=${ROUNDS}\n`);

for (let round = 1; round <= ROUNDS; round++) {
  for (const item of questions) {
    const r = await measure(item);
    samples.push({ round, question: item.q, symbol: item.symbol, ...r });
    const tag = r.ok ? `${String(r.total_ms).padStart(6)}ms` : `  FAIL ${r.status ?? ""} ${r.error ?? ""}`;
    const extra = r.usage ? ` prompt=${r.usage.prompt} out=${r.usage.output} thoughts=${r.usage.thoughts ?? "-"}` : "";
    console.log(`  r${round} ${tag}  ${item.q.slice(0, 52).padEnd(52)}${extra}`);
    await sleep(PAUSE_MS);
  }
}

// --- report ------------------------------------------------------------------

const ok = samples.filter((s) => s.ok);
const failed = samples.filter((s) => !s.ok);
const round1 = ok.filter((s) => s.round === 1).map((s) => s.total_ms);
const laterRounds = ok.filter((s) => s.round > 1).map((s) => s.total_ms);

const report = {
  measured_at: new Date().toISOString(),
  base: BASE,
  mode: MODE,
  rounds: ROUNDS,
  requests: samples.length,
  ok: ok.length,
  failed: failed.length,
  latency_ms: {
    all: summarize(ok.map((s) => s.total_ms)),
    round1_cold_cache: summarize(round1),
    rounds2plus_warm_cache: summarize(laterRounds),
  },
};

if (MODE === "ask") {
  const withUsage = ok.filter((s) => s.usage);
  report.time_to_sources_ms = summarize(ok.filter((s) => s.sources_ms != null).map((s) => s.sources_ms));
  report.time_to_first_token_ms = summarize(ok.filter((s) => s.first_delta_ms != null).map((s) => s.first_delta_ms));
  report.refused = ok.filter((s) => s.refused).length;
  report.tokens = {
    reported_by_provider_for: withUsage.length,
    provider: withUsage[0]?.usage?.provider ?? null,
    model: withUsage[0]?.usage?.model ?? null,
    prompt: summarize(withUsage.map((s) => s.usage.prompt)),
    output: summarize(withUsage.map((s) => s.usage.output)),
    thoughts: summarize(withUsage.filter((s) => s.usage.thoughts != null).map((s) => s.usage.thoughts)),
    total: summarize(withUsage.map((s) => s.usage.total)),
  };
} else {
  report.sources = summarize(ok.map((s) => s.sources));
  report.context_chars = summarize(ok.map((s) => s.context_chars));
  // Same estimator as src/lib/context.ts (CHARS_PER_TOKEN = 4).
  report.context_tokens_estimated = summarize(ok.map((s) => Math.ceil(s.context_chars / 4)));
}

console.log("\n" + JSON.stringify(report, null, 2));
if (failed.length) {
  console.log(`\n${failed.length} failed request(s):`);
  for (const f of failed.slice(0, 5)) console.log(`  ${f.status ?? ""} ${f.error}`);
}
if (OUT) {
  fs.writeFileSync(OUT, JSON.stringify({ report, samples }, null, 2));
  console.log(`\nraw samples -> ${OUT}`);
}
