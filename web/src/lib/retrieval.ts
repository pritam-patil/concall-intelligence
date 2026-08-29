import type { SupabaseClient } from "@supabase/supabase-js";
import { companyDropTerms, shapeKeywordQuery } from "./keywords";

/**
 * The retrieval /api/ask feeds the model — shared with /api/search's
 * mode:"ask" so eval/smoke.py measures exactly this, not an approximation.
 *
 * Two RPCs, run concurrently:
 *   - match_chunks_hybrid: vector similarity fused (RRF) with full-text search
 *     over a keyword-shaped query (lib/keywords.ts) — the passages the model
 *     actually gets. Its `score` is the fused rank score, not a similarity.
 *   - match_chunks_filtered with match_count 1: the top-1 COSINE similarity,
 *     which is what the confidence gate in /api/ask thresholds on. RRF scores
 *     are rank-based and carry no "relevant enough" meaning, so the gate keeps
 *     reading an absolute similarity — same semantics as before hybrid.
 *
 * Why not one RPC: match_chunks_hybrid doesn't return the cosine score, and
 * changing it means a migration on the hosted project; a second, cheap,
 * indexed top-1 lookup costs ~nothing and keeps the schema as-is.
 */

export type RetrievedChunk = {
  content: string;
  symbol: string;
  doc_type: string;
  period: string | null;
  /** ISO date NSE published the filing; dates a passage whose period is null. */
  filed_at: string | null;
  page: number | null;
  source_url: string;
  /** RRF fused score (rank-based; only meaningful for ordering). */
  score: number;
  vector_rank: number | null;
  text_rank: number | null;
};

export type RetrievalResult = {
  chunks: RetrievedChunk[];
  /** Top-1 cosine similarity from the vector channel — the gate's input. */
  maxScore: number;
  /** The websearch_to_tsquery text the keyword channel ran ("" = none). */
  keywordQuery: string;
};

export type RetrievalFilters = {
  symbol?: string;
  doc_type?: string;
  period?: string;
};

const FUSION_WEIGHT = Number(process.env.HYBRID_FUSION_WEIGHT ?? 0.5);

export async function retrieveForAsk(
  supabase: SupabaseClient,
  question: string,
  queryVector: number[],
  matchCount: number,
  filters: RetrievalFilters,
): Promise<RetrievalResult> {
  // A scoped question drops the company's own name words from the keyword
  // query (see lib/keywords.ts for why). One indexed primary-key read.
  let dropTerms: string[] = [];
  if (filters.symbol) {
    const { data } = await supabase
      .from("companies")
      .select("symbol, name")
      .eq("symbol", filters.symbol)
      .maybeSingle();
    if (data) dropTerms = companyDropTerms(data as { symbol: string; name: string });
  }
  const keywordQuery = shapeKeywordQuery(question, dropTerms);

  const rpcFilters = {
    filter_symbol: filters.symbol ?? null,
    filter_doc_type: filters.doc_type ?? null,
    filter_period: filters.period ?? null,
  };
  const [hybrid, gate] = await Promise.all([
    supabase.rpc("match_chunks_hybrid", {
      query_embedding: queryVector,
      query_text: keywordQuery,
      match_count: matchCount,
      fusion_weight: FUSION_WEIGHT,
      ...rpcFilters,
    }),
    supabase.rpc("match_chunks_filtered", {
      query_embedding: queryVector,
      match_count: 1,
      ...rpcFilters,
    }),
  ]);
  if (hybrid.error) throw new Error(hybrid.error.message);
  if (gate.error) throw new Error(gate.error.message);

  const top = (gate.data ?? []) as { score: number }[];
  return {
    chunks: (hybrid.data ?? []) as RetrievedChunk[],
    maxScore: top.length ? top[0].score : 0,
    keywordQuery,
  };
}
