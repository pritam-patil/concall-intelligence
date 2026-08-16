import { NextRequest, NextResponse } from "next/server";
import { getEmbeddingsProvider } from "@/lib/providers/embeddings";
import { getGenerationProvider } from "@/lib/providers/generation";
import { getServiceRoleClient } from "@/lib/supabase";

/**
 * POST /api/ask — source-cited Q&A over NSE filings and earnings-call
 * transcripts. See ../../../../ARCHITECTURE.md for the full data flow.
 *
 * Skeleton only: embed the question, run pgvector similarity search via the
 * `match_chunks` RPC, then ask the generation provider to answer strictly
 * from the retrieved, cited chunks.
 */
export async function POST(req: NextRequest) {
  const { question } = await req.json();
  if (!question || typeof question !== "string") {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  const embeddings = getEmbeddingsProvider();
  const generation = getGenerationProvider();
  const supabase = getServiceRoleClient();

  const [queryVector] = await embeddings.embed([question]);

  const { data: chunks, error } = await supabase.rpc("match_chunks", {
    query_embedding: queryVector,
    match_count: 8,
  });
  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  const context = (chunks ?? [])
    .map((c: { source: string; content: string }, i: number) => `[${i + 1}] (${c.source})\n${c.content}`)
    .join("\n\n");

  const answer = await generation.generate(
    `Question: ${question}\n\nContext:\n${context}\n\nAnswer using only the context above. Cite sources like [1], [2].`,
    "You are a precise financial research assistant. Never state a fact that isn't backed by the given context, and always cite it.",
  );

  return NextResponse.json({ answer, sources: chunks });
}
