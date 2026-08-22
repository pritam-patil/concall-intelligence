/**
 * Query shaping for the keyword half of hybrid retrieval.
 *
 * match_chunks_hybrid feeds its `query_text` to websearch_to_tsquery, which
 * ANDs every non-stopword term — so a natural-language question ("What are
 * Reliance's capital expenditure plans?") only matches a chunk containing
 * what ∧ reliance ∧ capital ∧ expenditure ∧ plan, which in practice is some
 * boilerplate paragraph, never the capex passage. (ingest/NOTES.md, "Hybrid
 * retrieval", recorded this failure mode; /api/ask hit it for real.)
 *
 * This turns the question into what the keyword channel is good at: an OR of
 * content terms, plus a few finance synonyms the filings use interchangeably
 * ("capital expenditure" ↔ "capex"). websearch syntax handles the rest —
 * `OR` is a disjunction and a "quoted phrase" is an adjacency match.
 *
 * When the question is scoped to a company, the company's own name words are
 * dropped (see companyDropTerms): "reliance" appears in most RELIANCE chunks,
 * and ts_rank rewards term frequency, so leaving it in promotes whichever
 * chunk says the company name most — measured, not assumed.
 */

// Function/question words and verbs of saying. Generic time words are dropped
// too ("year"/"quarter" are in nearly every chunk). Deliberately NOT dropped:
// finance nouns like revenue, margin, dividend — those are the content.
const STOPWORDS = new Set([
  "the", "a", "an", "of", "for", "on", "in", "to", "and", "or", "with", "at", "by",
  "from", "as", "into", "over", "under", "about", "regarding", "per", "vs", "versus",
  "during", "between", "after", "before", "since", "while", "within", "across", "through",
  "towards", "against", "including", "such", "also", "other", "each", "both", "than",
  "then", "but", "whether", "not", "only", "just", "still", "yet", "very",
  "is", "are", "was", "were", "be", "been", "being", "do", "does", "did", "has", "have",
  "had", "will", "would", "can", "could", "should", "may", "might",
  "what", "which", "who", "whom", "how", "why", "when", "where", "whats",
  "say", "said", "says", "tell", "told", "mention", "mentioned", "comment", "commented",
  "comments", "discuss", "discussed", "describe", "described", "note", "noted",
  "their", "its", "they", "them", "this", "that", "these", "those", "there", "any",
  "all", "some", "much", "many", "more", "most", "key", "main", "recent", "latest",
  "last", "next", "current", "year", "years", "quarter", "quarters", "quarterly",
  "fiscal", "annual", "management", "company", "companies", "limited", "ltd",
]);

// Phrases/terms the filings use interchangeably. Keys and values are
// lowercase; multi-word entries are emitted as quoted phrases.
const SYNONYMS: Record<string, string[]> = {
  "capital expenditure": ["capex"],
  capex: ["capital expenditure"],
  "net interest margin": ["nim"],
  nim: ["net interest margin"],
  "profit after tax": ["pat", "net profit"],
  "net profit": ["profit after tax", "pat"],
  "earnings per share": ["eps"],
  eps: ["earnings per share"],
  "total contract value": ["tcv", "deal wins"],
  "deal wins": ["tcv", "large deals"],
  tcv: ["total contract value", "deal wins"],
  "non performing assets": ["npa", "gross npa"],
  npa: ["non performing assets"],
  "return on equity": ["roe"],
  roe: ["return on equity"],
  "free cash flow": ["fcf"],
};

const MIN_TOKEN_LEN = 3;

function tokenize(text: string): string[] {
  return text.toLowerCase().match(/[a-z0-9]+/g) ?? [];
}

/**
 * Words to exclude from the keyword query when a question is scoped to this
 * company: its symbol and the words of its name (minus Limited/Ltd).
 */
export function companyDropTerms(company: { symbol: string; name: string }): string[] {
  return [company.symbol.toLowerCase(), ...tokenize(company.name)].filter(
    (t) => !STOPWORDS.has(t),
  );
}

/**
 * Build the websearch_to_tsquery text for a question: content terms OR'd
 * together, with synonym expansions. Returns "" when nothing survives (the
 * hybrid RPC then degrades to pure vector ranking — no keyword matches).
 */
export function shapeKeywordQuery(question: string, dropTerms: string[] = []): string {
  // Possessives ("Reliance's") → base word; hyphen-joined words stay joined
  // so "FY2025-26" tokenizes like the tsvector does.
  const lowered = question.toLowerCase().replace(/[’']s\b/g, "");
  const drop = new Set(dropTerms.map((t) => t.toLowerCase()));
  const terms: string[] = [];
  const seen = new Set<string>();
  const add = (t: string) => {
    if (!seen.has(t)) {
      seen.add(t);
      terms.push(t);
    }
  };

  // Multi-word synonym keys first, so their phrase form leads the query.
  for (const [key, expansions] of Object.entries(SYNONYMS)) {
    if (key.includes(" ") && lowered.includes(key)) {
      add(key);
      expansions.forEach(add);
    }
  }

  for (const token of tokenize(lowered)) {
    if (token.length < MIN_TOKEN_LEN || STOPWORDS.has(token) || drop.has(token)) continue;
    add(token);
    const expansions = SYNONYMS[token];
    if (expansions && !token.includes(" ")) expansions.forEach(add);
  }

  return terms.map((t) => (t.includes(" ") ? `"${t}"` : t)).join(" OR ");
}
