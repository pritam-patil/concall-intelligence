import { NextRequest, NextResponse } from "next/server";
import { getEmbeddingsProvider } from "@/lib/providers/embeddings";
import { getServiceRoleClient } from "@/lib/supabase";

/**
 * POST /api/search — plain semantic search over chunks, with optional
 * metadata filters. No generation step (that's /api/ask) — this returns
 * ranked chunks and their source metadata directly, for a search UI or
 * for debugging retrieval quality on its own.
 *
 * Body: { query: string, symbol?: string, doc_type?: "annual_report" |
 * "concall" | "announcement", period?: string, top_k?: number (default 10) }
 *
 * Runs the query through the SAME EmbeddingsProvider ingest/ used to embed
 * the chunks (EMBEDDINGS_PROVIDER — see lib/providers/embeddings.ts and
 * ARCHITECTURE.md §3.2) — a query embedded by a different model would
 * make every cosine-distance score meaningless, not just wrong. Filtering
 * and cosine ranking both happen in one query, via match_chunks_filtered
 * (supabase/migrations) — a second, purpose-built RPC alongside
 * match_chunks (which /api/ask uses): same join and ordering, but raw
 * (symbol, doc_type, period, source_url) fields instead of a pre-built
 * citation string, and three optional filters match_chunks has no need
 * for.
 */

const DOC_TYPES = new Set(["annual_report", "concall", "announcement"]);

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  if (!body || typeof body.query !== "string" || !body.query.trim()) {
    return NextResponse.json({ error: "query is required" }, { status: 400 });
  }
  const { query, symbol, doc_type, period, top_k } = body;

  if (doc_type !== undefined && !DOC_TYPES.has(doc_type)) {
    return NextResponse.json(
      { error: `doc_type must be one of ${[...DOC_TYPES].join(", ")}` },
      { status: 400 },
    );
  }
  if (top_k !== undefined && (typeof top_k !== "number" || top_k <= 0)) {
    return NextResponse.json({ error: "top_k must be a positive number" }, { status: 400 });
  }

  const embeddings = getEmbeddingsProvider();
  const supabase = getServiceRoleClient();

  // Wrapped so a provider failure (a bad API key, a free-tier quota hit —
  // both real, both hit while building this — see ingest/NOTES.md) still
  // returns the same {error} JSON shape as the RPC error path below,
  // instead of Next.js's default unhandled-exception response, which has
  // no body a JSON API caller can parse.
  let queryVector: number[];
  try {
    [queryVector] = await embeddings.embed([query]);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `embedding failed: ${message}` }, { status: 502 });
  }

  const { data, error } = await supabase.rpc("match_chunks_filtered", {
    query_embedding: queryVector,
    match_count: top_k ?? 10,
    filter_symbol: symbol ?? null,
    filter_doc_type: doc_type ?? null,
    filter_period: period ?? null,
  });

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ query, results: data });
}
