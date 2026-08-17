#!/usr/bin/env node
/**
 * Tiny script: POST /api/search with three sample queries and print what
 * comes back. Not a Jest/Vitest suite — web/ has no test framework
 * configured, and one route isn't reason enough to add one; a plain fetch
 * script against a real running server is more honest about what this
 * actually checks (a real HTTP round trip, real embeddings, a real DB
 * query) than a mocked unit test would be anyway.
 *
 * Requires a running dev server with real Supabase + Cloudflare
 * credentials in web/.env.local, and a database that actually has
 * embedded chunks in it — see ingest/NOTES.md for the real, populated
 * run this was checked against.
 *
 *   npm run dev &
 *   node scripts/test-search.mjs
 *   SEARCH_API_URL=http://localhost:3000/api/search node scripts/test-search.mjs
 */

const API_URL = process.env.SEARCH_API_URL ?? "http://localhost:3000/api/search";

// Three distinct filter combinations on purpose: unfiltered, a symbol
// filter, and a combined doc_type+period filter — the point isn't just
// "does search work" but "do the filters actually narrow results".
const QUERIES = [
  { query: "risks related to global economic conditions" },
  { query: "AI investments and generative AI strategy", symbol: "INFY" },
  {
    query: "employee benefits and human resources",
    doc_type: "annual_report",
    period: "FY2025-26",
  },
];

async function runQuery(body) {
  const started = Date.now();
  let res;
  try {
    res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    console.log(`\n=== ${JSON.stringify(body)} ===`);
    console.log(`FAILED to reach ${API_URL}: ${err.message}`);
    return { ok: false };
  }
  const elapsed = Date.now() - started;
  const data = await res.json();

  console.log(`\n=== ${JSON.stringify(body)} ===`);
  console.log(`HTTP ${res.status} in ${elapsed}ms`);
  if (!res.ok) {
    console.log("ERROR:", data.error);
    return { ok: false };
  }

  console.log(`${data.results.length} result(s):`);
  for (const [i, r] of data.results.entries()) {
    const periodTag = r.period ? ` (${r.period})` : "";
    console.log(
      `  [${i + 1}] ${r.symbol} ${r.doc_type}${periodTag} p.${r.page} ` +
        `score=${r.score.toFixed(4)} — ${JSON.stringify(r.content.slice(0, 100))}...`,
    );
  }
  return { ok: true, results: data.results };
}

async function main() {
  let failures = 0;
  for (const q of QUERIES) {
    const { ok } = await runQuery(q);
    if (!ok) failures += 1;
  }
  console.log(`\n${QUERIES.length - failures}/${QUERIES.length} queries returned results`);
  process.exit(failures ? 1 : 0);
}

main();
