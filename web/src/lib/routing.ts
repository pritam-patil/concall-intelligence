import { searchSymbolsScored, type SymbolEntry } from "./symbols";

/**
 * Query routing: detect which covered company (if any) a question is about, so
 * /api/ask can be auto-scoped. Resolution uses the ported nse-mcp scorer
 * (lib/symbols.ts) over the COVERED companies only — see that file for why the
 * covered subset rather than the full EQUITY_L roster.
 *
 * Three outcomes:
 *  - scope:  exactly one company is confidently named → auto-apply its filter.
 *  - choose: the mention is ambiguous ("Tata" → 3 Tata cos) or several distinct
 *            companies are named ("Infosys and TCS") → ask the user to pick
 *            (v1 answers one company at a time).
 *  - none:   no company named → answer unscoped.
 */

export type RouteChoice = { symbol: string; name: string };

export type RouteDecision =
  | { kind: "none" }
  | { kind: "scope"; company: RouteChoice }
  | { kind: "choose"; reason: "ambiguous" | "multiple"; choices: RouteChoice[] };

// A confident identifier: exact symbol/name (1000/850), symbol/name-word prefix
// or exact word (≥700). Below this is only fuzzy/substring — too weak to
// auto-scope on (the resolver still tolerates typos; we just don't act on a
// low-confidence guess without asking).
const STRONG = 700;
const MIN_TOKEN_LEN = 3;

// Function/question words plus finance-question nouns that collide with a
// covered company's name word ("bank" → HDFC Bank, "services" → Tata
// Consultancy Services, "industries" → Reliance Industries). Dropping these is
// safe: each such company is still found by a distinctive token — "hdfc"
// (symbol prefix), "consultancy"/"tcs", "reliance". Distinctive auto/industry
// words ("motors", "vehicles", "passenger") are kept — they help split TMCV/TMPV.
const STOPWORDS = new Set([
  "the", "a", "an", "of", "for", "on", "in", "to", "and", "or", "with", "at", "by",
  "is", "are", "was", "were", "be", "been", "do", "does", "did", "has", "have", "had",
  "what", "which", "who", "how", "why", "when", "where", "whats",
  "say", "said", "says", "tell", "told", "mention", "mentioned", "comment", "comments",
  "about", "regarding", "their", "its", "they", "this", "that", "these", "those",
  "last", "recent", "latest", "quarter", "quarterly", "annual", "year", "fy",
  "revenue", "revenues", "margin", "margins", "growth", "guidance", "outlook",
  "results", "result", "report", "reports", "dividend", "demand", "risk", "risks",
  "business", "market", "share", "shares", "price", "earnings", "call", "transcript",
  "filing", "filings", "management", "board", "company", "companies", "limited", "ltd",
  "bank", "services", "industries",
]);

/** Tokenise to lowercase alphanumeric (+ '&') words. */
function tokenize(question: string): string[] {
  return question.toLowerCase().match(/[a-z0-9&]+/g) ?? [];
}

function toChoice(entry: SymbolEntry): RouteChoice {
  return { symbol: entry.symbol, name: entry.name };
}

/**
 * Decide how to scope `question` given the covered companies. Pure — the caller
 * passes the covered list (from the DB, which carries EQUITY_L names).
 */
export function routeQuestion(question: string, companies: RouteChoice[]): RouteDecision {
  const entries: SymbolEntry[] = companies.map((c) => ({ symbol: c.symbol, name: c.name }));
  const bySymbol = new Map(entries.map((e) => [e.symbol, e]));
  const tokens = tokenize(question);

  // A span that resolves to exactly one company is "decisive"; one that ties
  // across several is an "ambiguous group" (e.g. "tata").
  const decisive = new Map<string, number>();
  const ambiguousGroups: string[][] = [];

  for (let n = 1; n <= 3; n++) {
    for (let i = 0; i + n <= tokens.length; i++) {
      const parts = tokens.slice(i, i + n);
      if (parts.every((p) => STOPWORDS.has(p) || p.length < MIN_TOKEN_LEN)) continue;
      const scored = searchSymbolsScored(entries, parts.join(" "), entries.length).filter(
        (s) => s.score >= STRONG,
      );
      if (scored.length === 0) continue;
      const top = scored[0].score;
      const winners = scored.filter((s) => s.score === top).map((s) => s.entry.symbol);
      if (winners.length === 1) {
        decisive.set(winners[0], Math.max(decisive.get(winners[0]) ?? 0, top));
      } else {
        ambiguousGroups.push(winners);
      }
    }
  }

  const decisiveSyms = [...decisive.keys()].sort(
    (a, b) => (decisive.get(b) ?? 0) - (decisive.get(a) ?? 0) || a.localeCompare(b),
  );

  if (decisiveSyms.length >= 2) {
    return { kind: "choose", reason: "multiple", choices: decisiveSyms.map((s) => toChoice(bySymbol.get(s)!)) };
  }

  if (decisiveSyms.length === 1) {
    const only = decisiveSyms[0];
    // A separate ambiguous mention that doesn't include the decisive company
    // means another company was named too → treat as multiple.
    const disjoint = ambiguousGroups.find((g) => !g.includes(only));
    if (disjoint) {
      const syms = [only, ...disjoint.filter((s) => s !== only)];
      return { kind: "choose", reason: "multiple", choices: syms.map((s) => toChoice(bySymbol.get(s)!)) };
    }
    return { kind: "scope", company: toChoice(bySymbol.get(only)!) };
  }

  // No decisive company. If a mention was ambiguous, ask among the most
  // specific (smallest) group — e.g. "tata motors" → TMCV/TMPV, not all Tatas.
  if (ambiguousGroups.length > 0) {
    const group = [...ambiguousGroups].sort((a, b) => a.length - b.length)[0];
    if (group.length >= 2) {
      return { kind: "choose", reason: "ambiguous", choices: group.map((s) => toChoice(bySymbol.get(s)!)) };
    }
  }

  return { kind: "none" };
}
