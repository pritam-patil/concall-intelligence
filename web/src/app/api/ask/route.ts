import { NextRequest, NextResponse } from "next/server";
import { getEmbeddingsProvider } from "@/lib/providers/embeddings";
import { getGenerationProvider } from "@/lib/providers/generation";
import { getServiceRoleClient } from "@/lib/supabase";

/**
 * POST /api/ask — source-cited, grounded Q&A over NSE filings and
 * earnings-call transcripts. See ../../../../ARCHITECTURE.md for the full
 * data flow; this is the online read path (§4).
 *
 * Body: { question: string, symbol?, doc_type?, period?, top_k? }
 *
 * Pipeline: embed the question (same provider ingest/ embedded chunks
 * with) -> vector similarity search (match_chunks_filtered — a real cosine
 * `score`, NOT the hybrid RRF RPC: the confidence gate below needs an
 * absolute similarity, and RRF fusion scores are rank-based and have no
 * "relevant enough" meaning) -> if the best chunk is below the confidence
 * threshold, refuse without spending an LLM call -> otherwise stream a
 * Gemini answer grounded strictly in the retrieved passages.
 *
 * TWO refusal paths, one phrase ("not found in the covered filings"):
 *   1. Low retrieval confidence — max score < ASK_SIMILARITY_THRESHOLD.
 *      Decided here, in code, before any generation call.
 *   2. Retrieval passed the gate but the passages still don't answer the
 *      question — enforced by the system instruction (rule 3). The model
 *      emits the same phrase, so the UI treats both identically.
 *
 * RESPONSE is a stream (NDJSON, one JSON object per line — see
 * web/README.md for the wire format). The cited chunks are emitted FIRST,
 * as a `sources` event, so the UI can render citations before/while the
 * answer text streams in as `delta` events:
 *   {"type":"sources","sources":[...],"max_score":..,"threshold":..}
 *   {"type":"delta","text":"..."}          (repeated)
 *   {"type":"done","refused":false}
 * A generation failure AFTER the stream has started can't change the HTTP
 * status (already 200), so it surfaces in-band as {"type":"error",...}.
 * Failures BEFORE streaming (bad input, embedding, the DB query) return a
 * normal JSON error with a real status code, exactly like /api/search.
 */

const DOC_TYPES = new Set(["annual_report", "concall", "announcement"]);

const DEFAULT_TOP_K = Number(process.env.ASK_TOP_K ?? 8);
// Cosine similarity (1 - distance) of the single best chunk must clear this
// or we refuse without generating. STRONGLY PROVIDER-DEPENDENT — MEASURED
// against this project's own data by the smoke eval (eval/smoke.py; full
// numbers in ingest/NOTES.md "Smoke eval and tuning"):
//   - Cloudflare bge (the PINNED default): answerable questions floor at
//     ~0.72 (range 0.72-0.88), but the OFF-topic floor is nearly as high —
//     random tokens 0.65, an off-topic real question 0.61, and a
//     plausible-but-absent one ("cryptocurrency strategy") 0.72, ABOVE some
//     real questions. The on/off gap is razor-thin, so NO gate value cleanly
//     separates them; the LLM grounding gate (system rule 3) is what refuses
//     plausible-absent questions correctly.
//   - Gemini embeddings (fallback): on-topic ~0.63-0.73, off-topic floor
//     ~0.40-0.44 — a lower, slightly wider band; ~0.50 fits there.
// 0.60 is tuned for the pinned bge provider as a CONSERVATIVE backstop: a
// ~0.12 margin below the 0.72 answerable floor means it (almost) never
// false-refuses a real question — and a code-path false-refusal is final
// (there's no LLM call left to recover it). It only catches genuinely
// degenerate retrieval. The grounding gate does the real refusal work.
// 0.65-0.70 is the aggressive alternative if saving generation calls matters
// (the Gemini free tier is 20 generations/day — see NOTES), at the cost of a
// thin false-refusal margin. Retune per embedding model via env.
const SIMILARITY_THRESHOLD = Number(process.env.ASK_SIMILARITY_THRESHOLD ?? 0.6);

const REFUSAL = "not found in the covered filings";

const SYSTEM_INSTRUCTION = [
  "You are a precise research assistant answering questions about Indian-market",
  "company filings and earnings-call transcripts. Follow these rules in priority order:",
  "",
  "1. Answer ONLY from the numbered context passages provided in the user message.",
  "   Never use outside knowledge or general information about the company, even if",
  "   you are confident it is correct.",
  "2. Every factual claim MUST be followed by a citation of the form",
  "   [doc_type, period, page], copied from the passage you used — for example",
  "   [annual_report, FY2025-26, p.276]. If a passage's period is empty, write it as",
  "   [concall, n/a, p.10]. A sentence with no citation is not allowed.",
  `3. If the context does not contain the answer, reply with exactly: "${REFUSAL}"`,
  "   and nothing else. Do not apologise, speculate, or explain what is missing.",
  "4. Never give buy, sell, or hold recommendations, price targets, or any personalised",
  "   investment or trading advice, even if asked directly. Answer only the factual,",
  "   citable parts of such a question; for the advice itself, state that you do not",
  "   provide investment advice.",
].join("\n");

type MatchedChunk = {
  content: string;
  symbol: string;
  doc_type: string;
  period: string | null;
  page: number | null;
  source_url: string;
  score: number;
};

function buildContext(chunks: MatchedChunk[]): string {
  return chunks
    .map((c, i) => {
      const period = c.period ?? "n/a";
      const page = c.page ?? "n/a";
      const header = `[${i + 1}] (doc_type=${c.doc_type}, period=${period}, page=${page}, symbol=${c.symbol})`;
      return `${header}\n${c.content}`;
    })
    .join("\n\n");
}

// Last-resort fallback for when EVERY generation provider fails (all free-tier
// quotas exhausted — Gemini's daily cap AND the Cloudflare failover). Retrieval
// runs on a separate budget (Cloudflare bge + pgvector), so we still hold the
// cited passages; rather than only erroring, degrade to an extractive answer —
// a short notice plus the top passages, each carrying the same
// [doc_type, period, page] citation the model would have used. The `sources`
// event already emitted the full list, so the UI's citation chips show too.
const EXTRACTIVE_MAX_PASSAGES = 3;
const EXTRACTIVE_SNIPPET_CHARS = 400;

function buildExtractiveAnswer(chunks: MatchedChunk[]): string {
  const intro =
    "⚠️ Automated answer synthesis is temporarily unavailable (generation quota reached). " +
    "Here are the most relevant passages from the covered filings — cited, so you can verify them directly:";
  const passages = chunks.slice(0, EXTRACTIVE_MAX_PASSAGES).map((c, i) => {
    const period = c.period ?? "n/a";
    const page = c.page ?? "n/a";
    const body = c.content.replace(/\s+/g, " ").trim();
    const snippet =
      body.length > EXTRACTIVE_SNIPPET_CHARS ? `${body.slice(0, EXTRACTIVE_SNIPPET_CHARS)}…` : body;
    return `${i + 1}. ${snippet} [${c.doc_type}, ${period}, p.${page}]`;
  });
  return [intro, "", ...passages].join("\n");
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  if (!body || typeof body.question !== "string" || !body.question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }
  const { question, symbol, doc_type, period, top_k } = body;

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
  const generation = getGenerationProvider();
  const supabase = getServiceRoleClient();

  // Pre-flight (embedding + retrieval) happens before the stream, so these
  // failures can still be honest HTTP errors rather than in-band events.
  let queryVector: number[];
  try {
    [queryVector] = await embeddings.embed([question]);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `embedding failed: ${message}` }, { status: 502 });
  }

  const { data, error } = await supabase.rpc("match_chunks_filtered", {
    query_embedding: queryVector,
    match_count: top_k ?? DEFAULT_TOP_K,
    filter_symbol: symbol ?? null,
    filter_doc_type: doc_type ?? null,
    filter_period: period ?? null,
  });
  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  const chunks = (data ?? []) as MatchedChunk[];
  // Results come back ordered by score desc, so the first is the max.
  const maxScore = chunks.length ? chunks[0].score : 0;
  const refused = chunks.length === 0 || maxScore < SIMILARITY_THRESHOLD;

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const emit = (obj: unknown) =>
        controller.enqueue(encoder.encode(JSON.stringify(obj) + "\n"));

      // Declared out here so the catch can tell "no output at all" (→ the
      // extractive fallback) from "died mid-answer" (→ an interruption note).
      let emittedDelta = false;

      try {
        // Refusal shows no sources — "not found in the covered filings"
        // and a list of sources would contradict each other. max_score is
        // still reported so the UI/debugging can see why it refused.
        emit({
          type: "sources",
          sources: refused ? [] : chunks,
          max_score: maxScore,
          threshold: SIMILARITY_THRESHOLD,
        });

        if (refused) {
          emit({ type: "delta", text: REFUSAL });
          emit({ type: "done", refused: true });
          controller.close();
          return;
        }

        const userPrompt = [
          `Question: ${question}`,
          "",
          "Context passages:",
          buildContext(chunks),
          "",
          "Answer the question using only these passages. Cite every claim as [doc_type, period, page].",
        ].join("\n");

        for await (const delta of generation.generateStream(userPrompt, SYSTEM_INSTRUCTION)) {
          emittedDelta = true;
          emit({ type: "delta", text: delta });
        }
        emit({ type: "done", refused: false });
        controller.close();
      } catch (err) {
        // Past this point the 200 and headers are already sent — the only way
        // to react is an in-band event, not an HTTP status.
        const message = err instanceof Error ? err.message : String(err);
        if (emittedDelta) {
          // A partial answer already streamed; generation can't be cleanly
          // restarted mid-stream, so note the interruption rather than
          // contradicting what's already on screen.
          console.warn(`[ask] generation failed mid-stream after partial output: ${message}`);
          emit({ type: "delta", text: "\n\n_(The answer was cut off — generation was interrupted.)_" });
          emit({ type: "done", refused: false });
        } else {
          // Nothing generated at all — every provider failed / was exhausted.
          // Degrade to the cited passages we already retrieved.
          console.warn(`[ask] generation produced no output, serving extractive fallback: ${message}`);
          emit({ type: "delta", text: buildExtractiveAnswer(chunks) });
          emit({ type: "done", refused: false });
        }
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
