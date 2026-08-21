/**
 * NSE symbol resolver — ported directly from the nse-mcp server's
 * `src/data/symbols.ts` so both products resolve company names to tickers with
 * the SAME rules (Project 1's dogfooding showed name-vs-ticker resolution is
 * where users and models stumble). The scoring here is byte-for-byte the MCP's;
 * only the Cloudflare-KV/HTTP plumbing (weekly refresh, storage) is dropped —
 * this app resolves over a small, in-memory entry list rather than the full
 * EQUITY_L roster (see lib/routing.ts for why the covered subset, not the full
 * ~2400-row list: "reliance"/"tata" match many listed entities and would make
 * every query ambiguous).
 *
 * `EQUITY_L.csv` is NSE's equity master (SYMBOL, NAME OF COMPANY, …); the
 * companies we cover are rows from it, so resolving over them matches the MCP.
 */

export type SymbolEntry = {
  symbol: string;
  name: string;
};

/** Parse one CSV line, honouring double-quoted fields (rare, but cheap insurance). */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      out.push(field);
      field = "";
    } else {
      field += ch;
    }
  }
  out.push(field);
  return out;
}

/**
 * Parse EQUITY_L.csv into symbol entries. Columns are
 * `SYMBOL, NAME OF COMPANY, SERIES, …`; only the first two are kept, the header
 * row is skipped, and rows without both a symbol and a name are dropped.
 */
export function parseEquityCsv(csv: string): SymbolEntry[] {
  const lines = csv.split(/\r?\n/);
  const entries: SymbolEntry[] = [];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    const cols = splitCsvLine(line);
    const symbol = (cols[0] ?? "").trim();
    const name = (cols[1] ?? "").trim();
    if (symbol && name) entries.push({ symbol, name });
  }
  return entries;
}

// ---- search ----------------------------------------------------------------

/** Levenshtein distance, capped: returns `max + 1` once it is provably exceeded. */
function boundedLevenshtein(a: string, b: string, max: number): number {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  const prev = new Array(b.length + 1);
  const curr = new Array(b.length + 1);
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    curr[0] = i;
    let rowMin = curr[0];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
      if (curr[j] < rowMin) rowMin = curr[j];
    }
    if (rowMin > max) return max + 1;
    for (let j = 0; j <= b.length; j++) prev[j] = curr[j];
  }
  return prev[b.length];
}

/** 0–1 similarity from edit distance, relative to the longer string. */
function similarity(a: string, b: string): number {
  const longer = Math.max(a.length, b.length);
  if (longer === 0) return 1;
  const max = Math.ceil(longer * 0.5);
  const dist = boundedLevenshtein(a, b, max);
  return dist > max ? 0 : 1 - dist / longer;
}

/** True if every char of `q` appears in `s` in order (typo-tolerant loose match). */
function isSubsequence(q: string, s: string): boolean {
  let i = 0;
  for (let j = 0; j < s.length && i < q.length; j++) {
    if (s[j] === q[i]) i++;
  }
  return i === q.length;
}

/**
 * Score one entry against a lowercased query.
 *
 * Substring matches occupy the high bands (400–1000) and always outrank fuzzy
 * matches (≤200), so an exact-ish hit is never buried under a typo-tolerant one.
 * Returns 0 for no match.
 */
function scoreEntry(q: string, entry: SymbolEntry): number {
  const sym = entry.symbol.toLowerCase();
  const nm = entry.name.toLowerCase();

  if (sym === q) return 1000;
  if (sym.startsWith(q)) return 900 - (sym.length - q.length);
  if (nm === q) return 850;

  const words = nm.split(/[^a-z0-9]+/).filter(Boolean);
  if (words.includes(q)) return 820;
  if (words.some((w) => w.startsWith(q))) return 700;
  if (sym.includes(q)) return 600 - sym.indexOf(q);
  if (nm.includes(q)) return 500 - Math.min(nm.indexOf(q), 99);

  // Fuzzy fallback for typos, kept strictly below the substring bands.
  let fuzzy = 0;
  const symSim = similarity(q, sym);
  if (symSim >= 0.6) fuzzy = Math.max(fuzzy, Math.round(symSim * 200));
  for (const w of words) {
    if (Math.abs(w.length - q.length) > 2) continue;
    const s = similarity(q, w);
    if (s >= 0.7) fuzzy = Math.max(fuzzy, Math.round(s * 150));
  }
  if (fuzzy === 0 && q.length >= 3 && isSubsequence(q, sym)) fuzzy = 120;
  return fuzzy;
}

export type ScoredEntry = { entry: SymbolEntry; score: number };

/**
 * Score every entry against `query` and return the positives, highest score
 * first, ties broken alphabetically by symbol. This is the scored core that
 * `searchSymbols` (below) returns entries from; routing (lib/routing.ts) uses
 * the scores to threshold on confidence.
 */
export function searchSymbolsScored(
  entries: SymbolEntry[],
  query: string,
  limit = 5,
): ScoredEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];

  const scored: ScoredEntry[] = [];
  for (const entry of entries) {
    const score = scoreEntry(q, entry);
    if (score > 0) scored.push({ entry, score });
  }

  scored.sort((a, b) => b.score - a.score || a.entry.symbol.localeCompare(b.entry.symbol));
  return scored.slice(0, limit);
}

/**
 * Case-insensitive substring + simple fuzzy search over symbol and company name.
 * Returns the best matches, highest score first, ties broken alphabetically —
 * identical behaviour to the nse-mcp `searchSymbols`.
 */
export function searchSymbols(entries: SymbolEntry[], query: string, limit = 5): SymbolEntry[] {
  return searchSymbolsScored(entries, query, limit).map((s) => s.entry);
}
