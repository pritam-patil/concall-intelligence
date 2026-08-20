/**
 * Static per-company suggested questions for the empty chat state — 3 per
 * symbol "for now" (v1). Hand-written to be answerable from the ingested
 * corpus (one FY2025-26 annual report + one recent concall per company).
 * When the corpus grows, these should be generated, not hand-maintained —
 * hence the single lookup point.
 */

const DEFAULT_SUGGESTIONS = [
  "What were the key financial highlights last quarter?",
  "What did management say about the demand outlook?",
  "What dividend did the board recommend for FY2025-26?",
];

const BY_SYMBOL: Record<string, string[]> = {
  RELIANCE: [
    "What did management say about the O2C and retail segments?",
    "What are Reliance's capital expenditure plans?",
    "What dividend did the board recommend for FY2025-26?",
  ],
  TCS: [
    "What did TCS management say about deal wins (TCV) and margins?",
    "How did TCS describe demand across BFSI and other verticals?",
    "What dividend did TCS's board recommend for FY2025-26?",
  ],
  HDFCBANK: [
    "What did management say about net interest margin (NIM)?",
    "How did deposits and advances grow last quarter?",
    "What dividend did the board recommend for FY2025-26?",
  ],
  INFY: [
    "What did Infosys say about its FY2025-26 revenue growth guidance?",
    "How did Infosys describe large-deal momentum?",
    "What dividend did the board recommend for FY2025-26?",
  ],
  TMCV: [
    "What did management say about commercial vehicle demand?",
    "What did management say about margins and EBITDA?",
    "What are the key risks noted in the annual report?",
  ],
  TMPV: [
    "What did management say about passenger vehicle and EV demand?",
    "What did management say about margins and profitability?",
    "What are the key risks noted in the annual report?",
  ],
};

/** 3 suggested questions for the given symbol, or a generic set for "all
 * companies" (empty symbol) / any symbol without a bespoke list. */
export function getSuggestions(symbol: string): string[] {
  return BY_SYMBOL[symbol] ?? DEFAULT_SUGGESTIONS;
}
