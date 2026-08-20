/**
 * Client-side reader for POST /api/ask's NDJSON stream (one JSON object per
 * line). The route emits, in order: one `sources` event, then `delta` events
 * as the answer streams, then `done` — or an in-band `error` if generation
 * fails after the stream has started (the HTTP 200 is already sent by then).
 * Failures BEFORE streaming (bad input, embedding, the DB query) come back as
 * a normal JSON error with a real status code, which we surface by throwing.
 *
 * See web/README.md's "/api/ask" section and web/scripts/test-ask.mjs for the
 * wire format this mirrors. A POST body rules out EventSource, so we read the
 * body stream directly.
 */

export type Source = {
  content: string;
  symbol: string;
  doc_type: string;
  period: string | null;
  page: number | null;
  source_url: string;
  score: number;
};

export type AskRequest = {
  question: string;
  symbol?: string;
  doc_type?: string;
  period?: string;
  top_k?: number;
};

export type AskEvent =
  | { type: "sources"; sources: Source[]; max_score: number; threshold: number }
  | { type: "delta"; text: string }
  | { type: "done"; refused: boolean }
  | { type: "error"; error: string };

/**
 * POST the question and invoke `onEvent` for each streamed event. Resolves
 * when the stream ends; throws on a pre-stream error (non-2xx / non-NDJSON
 * response) or if the request is aborted via `signal`.
 */
export async function streamAsk(
  body: AskRequest,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  // Pre-flight errors are JSON with a real status code, not a stream — detect
  // by content type, exactly like scripts/test-ask.mjs does.
  const contentType = res.headers.get("content-type") ?? "";
  if (!res.ok || !contentType.includes("x-ndjson") || !res.body) {
    let message = `Request failed (HTTP ${res.status})`;
    try {
      const data = await res.json();
      if (data?.error) message = data.error;
    } catch {
      // non-JSON body — keep the status-code message
    }
    throw new Error(message);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushLine = (line: string) => {
    const trimmed = line.trim();
    if (trimmed) onEvent(JSON.parse(trimmed) as AskEvent);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) !== -1) {
      flushLine(buffer.slice(0, nl));
      buffer = buffer.slice(nl + 1);
    }
  }
  flushLine(buffer); // any trailing line without a newline
}
