#!/usr/bin/env node
/**
 * Tiny script: POST /api/ask and consume its NDJSON stream, printing the
 * cited sources, the streamed answer, and whether it refused. Same spirit
 * as test-search.mjs — a real HTTP round trip against a real running
 * server and a real (populated) DB, not a mocked unit test.
 *
 * Four scenarios on purpose, each exercising a distinct branch:
 *   1. Answerable        — should stream a cited answer.
 *   2. Off-topic         — should refuse via the SIMILARITY THRESHOLD
 *                          (code path, no LLM call): empty sources,
 *                          "not found in the covered filings".
 *   3. Buy/sell advice   — should answer citable facts but decline the
 *                          investment-advice part (system-instruction rule).
 *   4. Empty question    — should 400 BEFORE streaming (pre-flight error).
 *
 *   npm run dev &
 *   node scripts/test-ask.mjs
 *   ASK_API_URL=http://localhost:3000/api/ask node scripts/test-ask.mjs
 */

const API_URL = process.env.ASK_API_URL ?? "http://localhost:3000/api/ask";

const SCENARIOS = [
  { label: "answerable", body: { question: "What dividend did the board recommend?" } },
  { label: "off-topic (threshold refusal)", body: { question: "What is the boiling point of helium?" } },
  {
    label: "buy/sell advice (should decline the advice)",
    body: { question: "Should I buy this stock? Also, what was the profit after tax?" },
  },
  { label: "empty question (pre-flight 400)", body: { question: "  " } },
];

async function runScenario({ label, body }) {
  console.log(`\n=== ${label} ===`);
  console.log(`Q: ${JSON.stringify(body.question)}`);
  let res;
  try {
    res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    console.log(`FAILED to reach ${API_URL}: ${err.message}`);
    return { ok: false };
  }

  // Pre-flight errors come back as normal JSON with a real status code,
  // not as a stream — detect by content type.
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("x-ndjson")) {
    const data = await res.json().catch(() => ({}));
    console.log(`HTTP ${res.status} (non-streamed): ${JSON.stringify(data)}`);
    // A 400 here is the expected result for the empty-question scenario.
    return { ok: res.status === 400 && label.includes("400") };
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  let sources = null;
  let maxScore = null;
  let refused = null;
  let streamError = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      const evt = JSON.parse(line);
      if (evt.type === "sources") {
        sources = evt.sources;
        maxScore = evt.max_score;
      } else if (evt.type === "delta") {
        answer += evt.text;
      } else if (evt.type === "done") {
        refused = evt.refused;
      } else if (evt.type === "error") {
        streamError = evt.error;
      }
    }
  }

  console.log(`HTTP ${res.status} (streamed)`);
  console.log(`max_score: ${maxScore}  refused: ${refused}`);
  console.log(`sources (${sources?.length ?? 0}):`);
  for (const [i, s] of (sources ?? []).entries()) {
    console.log(
      `  [${i + 1}] ${s.symbol} ${s.doc_type} p.${s.page} score=${s.score.toFixed(4)} — ` +
        `${JSON.stringify(s.content.slice(0, 80))}...`,
    );
  }
  if (streamError) console.log(`STREAM ERROR: ${streamError}`);
  console.log(`answer:\n${answer}`);
  return { ok: !streamError };
}

async function main() {
  let failures = 0;
  for (const s of SCENARIOS) {
    const { ok } = await runScenario(s);
    if (!ok) failures += 1;
  }
  console.log(`\n${SCENARIOS.length - failures}/${SCENARIOS.length} scenarios OK`);
  process.exit(failures ? 1 : 0);
}

main();
