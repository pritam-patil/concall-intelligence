import { NextRequest, NextResponse } from "next/server";
import { getEmbeddingsProvider } from "@/lib/providers/embeddings";
import { getServiceRoleClient } from "@/lib/supabase";
import { MAX_QUESTION_CHARS, checkRateLimit, clientIp, ipHash, rateLimitMessage } from "@/lib/guard";
import { retrieveForAsk } from "@/lib/retrieval";
import { createLogger, requestId, stopwatch } from "@/lib/log";

/**
 * POST /api/search — semantic + keyword hybrid search over chunks, with
 * optional metadata filters. No generation step (that's /api/ask) — this
 * returns ranked chunks and their source metadata directly, for a search
 * UI or for debugging retrieval quality on its own.
 *
 * Body: { query: string, symbol?: string, doc_type?: "annual_report" |
 * "concall" | "announcement", period?: string, top_k?: number (default
 * HYBRID_TOP_K env, else 10), mode?: "hybrid" (default) | "vector" | "ask" }
 *
 * Runs the query through the SAME EmbeddingsProvider ingest/ used to embed
 * the chunks (EMBEDDINGS_PROVIDER — see lib/providers/embeddings.ts and
 * ARCHITECTURE.md §3.2) — a query embedded by a different model would
 * make every cosine-distance score meaningless, not just wrong.
 *
 * Default mode is "hybrid": vector similarity fused with Postgres
 * full-text search via reciprocal rank fusion (match_chunks_hybrid —
 * supabase/migrations/*_match_chunks_hybrid.sql), which measurably beats
 * vector-only on queries containing exact figures/proper nouns vector
 * embeddings tend to blur together (see ingest/NOTES.md's "Hybrid
 * retrieval" section for a real five-query comparison). mode="vector"
 * calls match_chunks_filtered instead — kept specifically so the two can
 * still be compared against a live server, the same way NOTES.md's
 * comparison was produced. HYBRID_FUSION_WEIGHT is env-only, not a body
 * field: like every other provider/model choice in this project, it's a
 * deployment-level default, not a per-request knob — see .env.example.
 *
 * mode="ask" is EXACTLY the retrieval /api/ask feeds the model (lib/
 * retrieval.ts: hybrid over a keyword-shaped query + the top-1 cosine the
 * confidence gate reads, returned as `max_score`) — so eval/smoke.py can
 * measure what ask sees rather than an approximation of it. The shaped
 * keyword query is echoed back as `keyword_query` for debugging.
 */

const DOC_TYPES = new Set(["annual_report", "concall", "announcement"]);
const MODES = new Set(["hybrid", "vector", "ask"]);

const DEFAULT_TOP_K = Number(process.env.HYBRID_TOP_K ?? 10);
const FUSION_WEIGHT = Number(process.env.HYBRID_FUSION_WEIGHT ?? 0.5);

// One embedding call + two RPCs; generous next to the default 10s.
export const maxDuration = 30;

export async function POST(req: NextRequest) {
  const log = createLogger({ route: "/api/search", request_id: requestId(req) });
  const total = stopwatch();
  const body = await req.json().catch(() => null);
  if (!body || typeof body.query !== "string" || !body.query.trim()) {
    return NextResponse.json({ error: "query is required" }, { status: 400 });
  }
  const { query, symbol, doc_type, period, top_k, mode = "hybrid" } = body;

  if (doc_type !== undefined && !DOC_TYPES.has(doc_type)) {
    return NextResponse.json(
      { error: `doc_type must be one of ${[...DOC_TYPES].join(", ")}` },
      { status: 400 },
    );
  }
  if (top_k !== undefined && (typeof top_k !== "number" || top_k <= 0)) {
    return NextResponse.json({ error: "top_k must be a positive number" }, { status: 400 });
  }
  if (!MODES.has(mode)) {
    return NextResponse.json(
      { error: `mode must be one of ${[...MODES].join(", ")}` },
      { status: 400 },
    );
  }

  // Abuse guardrails before spending an embedding call: length cap + per-IP
  // daily cap (shared with /api/ask — see web/src/lib/guard.ts).
  if (query.length > MAX_QUESTION_CHARS) {
    return NextResponse.json(
      { error: `Your query is too long (${query.length} characters). Please keep it under ${MAX_QUESTION_CHARS} characters.` },
      { status: 413 },
    );
  }
  const ip = clientIp(req);
  const rate = await checkRateLimit(ip);
  if (!rate.allowed) {
    log.info("search.rejected", { reason: "rate_limited", ip_hash: ipHash(ip), used: rate.used, limit: rate.limit });
    return NextResponse.json({ error: rateLimitMessage(rate.limit) }, { status: 429 });
  }

  const embeddings = getEmbeddingsProvider();
  const supabase = getServiceRoleClient();
  const fields = {
    mode,
    symbol: symbol ?? null,
    doc_type: doc_type ?? null,
    period: period ?? null,
    query_chars: query.length,
    ip_hash: ipHash(ip),
  };

  // Wrapped so a provider failure (a bad API key, a free-tier quota hit —
  // both real, both hit while building this — see ingest/NOTES.md) still
  // returns the same {error} JSON shape as the RPC error path below,
  // instead of Next.js's default unhandled-exception response, which has
  // no body a JSON API caller can parse.
  let queryVector: number[];
  const embedTimer = stopwatch();
  try {
    [queryVector] = await embeddings.embed([query]);
  } catch (err) {
    log.error("search.embed_failed", { ...fields, provider: embeddings.name, embed_ms: embedTimer(), err });
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `embedding failed: ${message}` }, { status: 502 });
  }
  const embed_ms = embedTimer();

  const matchCount = top_k ?? DEFAULT_TOP_K;

  if (mode === "ask") {
    try {
      const r = await retrieveForAsk(supabase, query, queryVector, matchCount, {
        symbol,
        doc_type,
        period,
      });
      log.info("search.complete", {
        ...fields,
        top_k: matchCount,
        results: r.chunks.length,
        max_score: r.maxScore,
        embed_ms,
        total_ms: total(),
      });
      return NextResponse.json({
        query,
        mode,
        keyword_query: r.keywordQuery,
        max_score: r.maxScore,
        results: r.chunks,
      });
    } catch (err) {
      log.error("search.retrieval_failed", { ...fields, embed_ms, total_ms: total(), err });
      const message = err instanceof Error ? err.message : String(err);
      return NextResponse.json({ error: message }, { status: 500 });
    }
  }

  const { data, error } =
    mode === "vector"
      ? await supabase.rpc("match_chunks_filtered", {
          query_embedding: queryVector,
          match_count: matchCount,
          filter_symbol: symbol ?? null,
          filter_doc_type: doc_type ?? null,
          filter_period: period ?? null,
        })
      : await supabase.rpc("match_chunks_hybrid", {
          query_embedding: queryVector,
          query_text: query,
          match_count: matchCount,
          fusion_weight: FUSION_WEIGHT,
          filter_symbol: symbol ?? null,
          filter_doc_type: doc_type ?? null,
          filter_period: period ?? null,
        });

  if (error) {
    log.error("search.retrieval_failed", { ...fields, embed_ms, total_ms: total(), err: new Error(error.message) });
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  log.info("search.complete", {
    ...fields,
    top_k: matchCount,
    results: Array.isArray(data) ? data.length : null,
    embed_ms,
    total_ms: total(),
  });
  return NextResponse.json({ query, mode, results: data });
}
