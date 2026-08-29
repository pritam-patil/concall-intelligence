/**
 * Unit checks for the two cost controls: the context-token cap
 * (src/lib/context.ts) and the query-embedding cache (src/lib/querycache.ts).
 *
 *     node scripts/test-budget.mjs
 *
 * Needs no server and no provider credentials — unlike test-ask.mjs /
 * test-search.mjs, which are integration scripts against a running app. Both
 * modules are pure logic, so they are worth checking directly; Node 24 strips
 * the TypeScript types on import, so there is no build step and no test
 * framework to add.
 */

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const lib = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "lib");
const { capContext, estimateTokens, passageTokens, CHARS_PER_TOKEN } = await import(
  path.join(lib, "context.ts")
);
const { embedQuery, queryCacheStats, resetQueryCache } = await import(
  path.join(lib, "querycache.ts")
);

let passed = 0;
function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ok  ${name}`);
  } catch (err) {
    console.error(`  FAIL ${name}\n       ${err.message}`);
    process.exitCode = 1;
  }
}
async function checkAsync(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ok  ${name}`);
  } catch (err) {
    console.error(`  FAIL ${name}\n       ${err.message}`);
    process.exitCode = 1;
  }
}

const chunk = (content, over = {}) => ({
  content,
  symbol: "INFY",
  doc_type: "concall",
  period: "Q1 FY27",
  filed_at: "2026-07-28",
  page: 6,
  ...over,
});

console.log("\ncontext: token estimation");

check("estimate is chars/CHARS_PER_TOKEN, rounded up", () => {
  assert.equal(estimateTokens("x".repeat(4 * CHARS_PER_TOKEN)), 4);
  assert.equal(estimateTokens("x"), 1);
  assert.equal(estimateTokens(""), 0);
});

check("passage cost includes the header, not just the content", () => {
  const c = chunk("hello");
  assert.ok(
    passageTokens(c, 0) > estimateTokens("hello"),
    "header line must be counted toward the budget",
  );
});

console.log("\ncontext: capping");

check("everything is kept when the budget is not binding", () => {
  const chunks = [chunk("a".repeat(400)), chunk("b".repeat(400))];
  const r = capContext(chunks, 10_000);
  assert.equal(r.kept.length, 2);
  assert.equal(r.dropped, 0);
});

check("drops from the tail, preserving rank order", () => {
  const chunks = [chunk("a".repeat(4000)), chunk("b".repeat(4000)), chunk("c".repeat(4000))];
  // ~1018 tokens each (4000 chars of content + the header line), so a 2100
  // budget admits exactly two and the third is dropped.
  const r = capContext(chunks, 2100);
  assert.equal(r.kept.length, 2);
  assert.equal(r.dropped, 1);
  assert.ok(r.kept[0].content.startsWith("a"), "highest-ranked passage kept first");
  assert.ok(r.kept[1].content.startsWith("b"));
});

check("reported token total matches the kept passages", () => {
  const chunks = [chunk("a".repeat(400)), chunk("b".repeat(400)), chunk("c".repeat(400))];
  const r = capContext(chunks, 10_000);
  const expected = r.kept.reduce((sum, c, i) => sum + passageTokens(c, i), 0);
  assert.equal(r.tokens, expected);
});

check("the top passage survives even alone over budget", () => {
  // A budget decision must never become a refusal — see capContext's comment.
  const r = capContext([chunk("a".repeat(40_000))], 10);
  assert.equal(r.kept.length, 1);
  assert.equal(r.dropped, 0);
});

check("empty retrieval caps to empty without throwing", () => {
  const r = capContext([], 6000);
  assert.deepEqual(r, { kept: [], dropped: 0, tokens: 0 });
});

check("passages never get truncated, only dropped", () => {
  const long = "a".repeat(4000);
  const r = capContext([chunk(long), chunk("b".repeat(4000))], 1100);
  assert.equal(r.kept[0].content, long, "kept passages must be byte-identical");
});

console.log("\nquerycache");

// A stub provider so the cache is tested without network or credentials.
function stubProvider(over = {}) {
  const p = {
    name: "cloudflare_bge",
    model: "@cf/baai/bge-base-en-v1.5",
    dimensions: 768,
    calls: 0,
    async embed(texts) {
      p.calls += 1;
      return texts.map(() => [0.1, 0.2, 0.3]);
    },
    ping: async () => {},
    ...over,
  };
  return p;
}

await checkAsync("a repeated question is embedded once", async () => {
  resetQueryCache();
  const p = stubProvider();
  const a = await embedQuery(p, "What was revenue?");
  const b = await embedQuery(p, "What was revenue?");
  assert.equal(p.calls, 1, "second ask must not call the provider");
  assert.equal(a.cached, false);
  assert.equal(b.cached, true);
  assert.deepEqual(b.vector, a.vector);
});

await checkAsync("different questions are embedded separately", async () => {
  resetQueryCache();
  const p = stubProvider();
  await embedQuery(p, "revenue?");
  await embedQuery(p, "margin?");
  assert.equal(p.calls, 2);
  assert.equal(queryCacheStats().size, 2);
});

await checkAsync("a different model never reuses another's vector", async () => {
  // The failure this guards is silent: a bge vector searched against a
  // Gemini-embedded corpus returns rankings that mean nothing, not an error.
  resetQueryCache();
  const bge = stubProvider();
  const gem = stubProvider({ name: "gemini", model: "gemini-embedding-001" });
  const first = await embedQuery(bge, "revenue?");
  const second = await embedQuery(gem, "revenue?");
  assert.equal(first.cached, false);
  assert.equal(second.cached, false, "must miss across providers");
  assert.equal(bge.calls, 1);
  assert.equal(gem.calls, 1);
});

await checkAsync("stats count hits and misses", async () => {
  resetQueryCache();
  const p = stubProvider();
  await embedQuery(p, "q1");
  await embedQuery(p, "q1");
  await embedQuery(p, "q2");
  const s = queryCacheStats();
  assert.equal(s.hits, 1);
  assert.equal(s.misses, 2);
});

await checkAsync("a provider failure propagates and is not cached", async () => {
  resetQueryCache();
  let attempts = 0;
  const p = stubProvider({
    async embed() {
      attempts += 1;
      throw new Error("quota exhausted");
    },
  });
  await assert.rejects(() => embedQuery(p, "q"), /quota exhausted/);
  await assert.rejects(() => embedQuery(p, "q"), /quota exhausted/);
  assert.equal(attempts, 2, "a failure must not be remembered as a result");
});

console.log(`\n${passed} check(s) passed${process.exitCode ? " — WITH FAILURES" : ""}\n`);
