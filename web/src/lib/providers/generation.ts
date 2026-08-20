/**
 * Generation (answer synthesis) provider interface — server-side only.
 * This is the project's "LLM provider" seam: the retrieval/prompting code
 * in the route handlers depends on this interface, never on a vendor SDK,
 * so a provider swap is a new class here plus a GENERATION_PROVIDER flip.
 *
 * Pinned default: Gemini Flash, free tier. Mirrors
 * ingest/src/ingest/providers/generation.py (the Python side has only the
 * non-streaming `generate` — streaming is a web/-only concern, since it's
 * the interactive /api/ask path that needs it, not the batch pipeline).
 *
 * `generate` returns the whole answer at once (:generateContent).
 * `generateStream` yields incremental text deltas (:streamGenerateContent
 * with `alt=sse`) for /api/ask, which streams tokens to the UI as Gemini
 * produces them rather than blocking on the full completion.
 */

export interface GenerationProvider {
  generate(prompt: string, system?: string): Promise<string>;
  generateStream(prompt: string, system?: string): AsyncIterable<string>;
}

type GeminiPayload = {
  contents: { role: string; parts: { text: string }[] }[];
  systemInstruction?: { parts: { text: string }[] };
};

function buildPayload(prompt: string, system?: string): GeminiPayload {
  const payload: GeminiPayload = {
    contents: [{ role: "user", parts: [{ text: prompt }] }],
  };
  if (system) {
    payload.systemInstruction = { parts: [{ text: system }] };
  }
  return payload;
}

class GeminiFlashGeneration implements GenerationProvider {
  private readonly baseUrl: string;
  private readonly apiKey: string;

  constructor(apiKey: string, model: string) {
    this.baseUrl = `https://generativelanguage.googleapis.com/v1beta/models/${model}`;
    this.apiKey = apiKey;
  }

  async generate(prompt: string, system?: string): Promise<string> {
    const res = await fetch(`${this.baseUrl}:generateContent?key=${this.apiKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload(prompt, system)),
    });
    if (!res.ok) {
      throw new Error(`Gemini generation call failed: ${res.status} ${await res.text()}`);
    }
    const data = await res.json();
    return data.candidates[0].content.parts[0].text as string;
  }

  /**
   * Opens the streaming connection, retrying transient statuses BEFORE the
   * body is consumed. Gemini Flash's free tier really does return transient
   * 503 "high demand" and 429s (both observed while building /api/ask) —
   * this is the streaming analogue of the Python side's tenacity retry
   * (ingest/.../generation.py). Retry is only safe pre-stream: once deltas
   * have been yielded there's no clean way to restart, so generateStream
   * never retries mid-body — a failure there surfaces to the caller, which
   * turns it into an in-band {type:"error"} event.
   */
  private async connectStream(prompt: string, system?: string): Promise<Response> {
    const RETRYABLE = new Set([429, 500, 503]);
    const MAX_ATTEMPTS = 3;
    const url = `${this.baseUrl}:streamGenerateContent?alt=sse&key=${this.apiKey}`;
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      if (attempt > 0) {
        await new Promise((resolve) => setTimeout(resolve, 600 * 2 ** (attempt - 1)));
      }
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildPayload(prompt, system)),
      });
      if (res.ok) {
        if (!res.body) throw new Error("Gemini streaming call returned no response body");
        return res;
      }
      const isLast = attempt === MAX_ATTEMPTS - 1;
      if (!RETRYABLE.has(res.status) || isLast) {
        throw new Error(`Gemini streaming call failed: ${res.status} ${await res.text()}`);
      }
      await res.body?.cancel(); // discard the error body before retrying
    }
    // Unreachable — the loop either returns or throws — but satisfies the type.
    throw new Error("Gemini streaming call failed after retries");
  }

  /**
   * Streams the answer as incremental text deltas. Gemini's SSE transport
   * (`alt=sse`) frames each partial as a `data: {json}` line; each JSON is
   * a normal generateContent chunk carrying the next slice of
   * candidates[0].content.parts[0].text. We buffer across reads (a single
   * network read can split a line, or carry several), parse only complete
   * lines, and yield each non-empty text slice. Anything that isn't a
   * `data:` line (SSE comments, blank separators) is skipped — as is a
   * frame whose only content is an empty-text "thought" part (Gemini's
   * thinking models emit these).
   */
  async *generateStream(prompt: string, system?: string): AsyncIterable<string> {
    const res = await this.connectStream(prompt, system);
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let newlineIdx: number;
        while ((newlineIdx = buffer.indexOf("\n")) !== -1) {
          const line = buffer.slice(0, newlineIdx).trim();
          buffer = buffer.slice(newlineIdx + 1);
          if (!line.startsWith("data:")) continue;
          const json = line.slice("data:".length).trim();
          if (!json || json === "[DONE]") continue;
          let parsed: unknown;
          try {
            parsed = JSON.parse(json);
          } catch {
            continue; // a partial JSON line shouldn't happen post line-split, but never throw mid-stream
          }
          const text = (parsed as GeminiStreamChunk)?.candidates?.[0]?.content?.parts?.[0]?.text;
          if (text) yield text;
        }
      }
    } finally {
      reader.releaseLock();
    }
  }
}

type GeminiStreamChunk = {
  candidates?: { content?: { parts?: { text?: string }[] } }[];
};

export function getGenerationProvider(): GenerationProvider {
  const provider = process.env.GENERATION_PROVIDER ?? "gemini_flash";
  if (provider === "gemini_flash") {
    const apiKey = process.env.GEMINI_API_KEY;
    const model = process.env.GEMINI_MODEL ?? "gemini-3.6-flash";
    if (!apiKey) {
      throw new Error("GEMINI_API_KEY is required for GENERATION_PROVIDER=gemini_flash");
    }
    return new GeminiFlashGeneration(apiKey, model);
  }
  throw new Error(`Unknown generation provider: ${provider}`);
}
