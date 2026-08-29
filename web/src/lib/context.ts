/**
 * Sizing and capping the context passages /api/ask sends the model.
 *
 * WHY A CAP: retrieved context is the dominant term in a question's cost —
 * measured at ~5,230 tokens of a ~6,250-token prompt (84%), against a
 * 609-token system instruction, so it is the only part worth trimming. Two
 * jobs: it bounds the worst
 * case (`top_k` is caller-supplied on a public endpoint, so without a cap
 * `{"top_k": 200}` is a request for a 100k-token prompt on someone else's
 * free tier), and at the shipped budget it also trims the ordinary case —
 * it engaged on 9 of 12 sampled questions and cut prompt tokens 27% on a
 * same-model A/B without changing a verdict. See COSTS.md for both numbers
 * and how they were taken.
 *
 * WHOLE PASSAGES ONLY: over budget, whole passages are dropped from the tail
 * (lowest fused rank first), never truncated. A half passage still looks
 * citable to the model, so truncation invites a citation whose supporting
 * sentence was cut off — a wrong answer that reads as a sourced one. Dropping
 * costs recall honestly instead.
 *
 * The caller MUST send the capped list to the UI as well as to the model:
 * citations are positional ([3] means "the third passage"), so a `sources`
 * event listing passages the model never saw would mis-resolve every marker
 * after the first drop.
 */

/**
 * Characters per token. MEASURED, not assumed: 20 real chunks from this
 * corpus (52,551 chars) came back from Gemini's countTokens as 12,828
 * tokens — 4.10 chars/token, or 1.43 tokens per whitespace word. 4 is that
 * measurement rounded DOWN, so the estimate runs ~2% high and the cap errs
 * toward sending less than the budget rather than more.
 *
 * Deliberately not a real tokenizer: every candidate ships a multi-megabyte
 * vocab (and `@google/generative-ai`'s countTokens is a network round-trip
 * per call), which is a lot to spend on a number that only decides where to
 * cut a list. ingest/chunk.py makes the same trade for the same reason,
 * though it counts words rather than characters.
 */
export const CHARS_PER_TOKEN = 4;

/** Approximate token count for a string. See CHARS_PER_TOKEN for the basis. */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / CHARS_PER_TOKEN);
}

/** The shape capContext needs — the retrieved-chunk fields that reach the prompt. */
export type Sized = {
  content: string;
  symbol: string;
  doc_type: string;
  period: string | null;
  filed_at: string | null;
  page: number | null;
};

/**
 * Per-passage overhead in the built context: the `[n] (doc_type=…, period=…,
 * filed=…, page=…, symbol=…)` header line plus the blank line between
 * passages. Measured from the real header format rather than guessed at, so
 * the budget covers what is actually sent.
 */
export function passageTokens(chunk: Sized, index: number): number {
  const header = `[${index + 1}] (doc_type=${chunk.doc_type}, period=${chunk.period ?? "n/a"}, filed=${chunk.filed_at ?? "n/a"}, page=${chunk.page ?? "n/a"}, symbol=${chunk.symbol})`;
  return estimateTokens(`${header}\n${chunk.content}\n\n`);
}

export type CappedContext<T> = {
  /** The passages that fit, in the order given (highest fused rank first). */
  kept: T[];
  /** How many were dropped for budget. */
  dropped: number;
  /** Estimated tokens of the kept passages, headers included. */
  tokens: number;
};

/**
 * The longest rank-ordered prefix of `chunks` fitting in `budget` tokens.
 *
 * The top passage is ALWAYS kept, even alone over budget: retrieval already
 * judged it the best evidence available, and returning nothing would turn a
 * budget decision into a refusal ("not found in the covered filings") that
 * the user would read as a claim about coverage. One oversized passage is a
 * bounded overrun — chunking caps a chunk at ~800 tokens (ingest/chunk.py) —
 * whereas a spurious refusal is a wrong answer.
 */
export function capContext<T extends Sized>(chunks: T[], budget: number): CappedContext<T> {
  const kept: T[] = [];
  let tokens = 0;
  for (const chunk of chunks) {
    const cost = passageTokens(chunk, kept.length);
    if (kept.length > 0 && tokens + cost > budget) break;
    kept.push(chunk);
    tokens += cost;
  }
  return { kept, dropped: chunks.length - kept.length, tokens };
}
