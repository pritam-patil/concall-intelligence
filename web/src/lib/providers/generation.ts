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

import { logger } from "../log";
import { pingWorkersAi, workersAiRunUrl } from "./cloudflare";
import { GEMINI_API_BASE, pingGeminiModel } from "./gemini";

/**
 * Token usage as the PROVIDER counted it, for the most recent call.
 *
 * Worth plumbing rather than estimating from the prompt text: gemini-3.6-flash
 * is a thinking model, and its reasoning tokens are billed but never appear in
 * either the prompt or the streamed answer — no amount of counting characters
 * on this side can see them. `thoughts` is null for providers that do not
 * report reasoning separately.
 */
export type TokenUsage = {
  provider: string;
  model: string;
  prompt: number;
  output: number;
  thoughts: number | null;
  total: number;
};

export interface GenerationProvider {
  /** Provider id as spelled in GENERATION_PROVIDER (e.g. "gemini_flash"). */
  readonly name: string;
  readonly model: string;
  generate(prompt: string, system?: string): Promise<string>;
  generateStream(prompt: string, system?: string): AsyncIterable<string>;
  /**
   * What the provider said the LAST generateStream call cost, or null if it
   * has not reported yet (no call made, or a provider/model that omits usage
   * from its stream). Read AFTER the stream is fully consumed.
   */
  lastUsage(): TokenUsage | null;
  /**
   * Quota-free reachability + auth probe for /api/health (a metadata call —
   * never a generation; Gemini's also verifies the model id, Workers AI's
   * can't — see providers/cloudflare.ts). Rejects with the reason.
   */
  ping(signal?: AbortSignal): Promise<void>;
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
  readonly name = "gemini_flash";
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private usage: TokenUsage | null = null;

  constructor(
    apiKey: string,
    readonly model: string,
  ) {
    this.baseUrl = `${GEMINI_API_BASE}/models/${model}`;
    this.apiKey = apiKey;
  }

  ping(signal?: AbortSignal): Promise<void> {
    return pingGeminiModel(this.apiKey, this.model, signal);
  }

  lastUsage(): TokenUsage | null {
    return this.usage;
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
          // usageMetadata rides along on the frames (cumulative, so the
          // last one seen is the total) — the only place the billed
          // thinking tokens are ever visible.
          const meta = (parsed as GeminiStreamChunk)?.usageMetadata;
          if (meta) {
            this.usage = {
              provider: this.name,
              model: this.model,
              prompt: meta.promptTokenCount ?? 0,
              output: meta.candidatesTokenCount ?? 0,
              thoughts: meta.thoughtsTokenCount ?? null,
              total: meta.totalTokenCount ?? 0,
            };
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
  usageMetadata?: {
    promptTokenCount?: number;
    candidatesTokenCount?: number;
    thoughtsTokenCount?: number;
    totalTokenCount?: number;
  };
};

// Workers AI text models vary in wire shape: older ones stream
// `{response: "..."}`, while the current OpenAI-compatible Llama models stream
// `{choices:[{delta:{content}}]}` with an empty top-level `response`. Read the
// delta first and fall back to `response`, so both shapes work.
type CloudflareStreamChunk = {
  response?: string;
  choices?: { delta?: { content?: string } }[];
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
};

/**
 * Cloudflare Workers AI text generation (free tier) — the failover provider
 * for when Gemini's daily quota is exhausted. Same `ai/run` endpoint + Bearer
 * auth as the embeddings provider (providers/embeddings.ts), so the existing
 * CLOUDFLARE_API_TOKEN already reaches it. Note this shares Cloudflare's daily
 * neuron budget with embeddings — it's a fallback, not a second workhorse.
 */
class CloudflareGeneration implements GenerationProvider {
  readonly name = "cloudflare";
  private readonly url: string;
  private readonly apiToken: string;
  private usage: TokenUsage | null = null;

  constructor(
    private readonly accountId: string,
    apiToken: string,
    readonly model: string,
  ) {
    this.url = workersAiRunUrl(accountId, model);
    this.apiToken = apiToken;
  }

  ping(signal?: AbortSignal): Promise<void> {
    return pingWorkersAi(this.accountId, this.apiToken, signal);
  }

  lastUsage(): TokenUsage | null {
    return this.usage;
  }

  private body(prompt: string, system: string | undefined, stream: boolean): string {
    const messages: { role: string; content: string }[] = [];
    if (system) messages.push({ role: "system", content: system });
    messages.push({ role: "user", content: prompt });
    return JSON.stringify({ messages, stream });
  }

  private headers(): Record<string, string> {
    return { Authorization: `Bearer ${this.apiToken}`, "Content-Type": "application/json" };
  }

  async generate(prompt: string, system?: string): Promise<string> {
    const res = await fetch(this.url, {
      method: "POST",
      headers: this.headers(),
      body: this.body(prompt, system, false),
    });
    if (!res.ok) {
      throw new Error(`Cloudflare generation call failed: ${res.status} ${await res.text()}`);
    }
    const data = await res.json();
    if (data?.success === false) {
      throw new Error(`Cloudflare generation call failed: ${JSON.stringify(data.errors)}`);
    }
    return (data.result.response ?? data.result.choices?.[0]?.message?.content) as string;
  }

  /**
   * Streams the answer. Workers AI's SSE transport frames each partial as a
   * `data: {json}` line whose JSON carries the next slice in `response`, and
   * ends with `data: [DONE]` — the same line-buffered read/parse shape as the
   * Gemini streamer above.
   */
  async *generateStream(prompt: string, system?: string): AsyncIterable<string> {
    const res = await fetch(this.url, {
      method: "POST",
      headers: this.headers(),
      body: this.body(prompt, system, true),
    });
    if (!res.ok || !res.body) {
      throw new Error(`Cloudflare streaming call failed: ${res.status} ${await res.text()}`);
    }
    const reader = res.body.getReader();
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
            continue;
          }
          const chunk = parsed as CloudflareStreamChunk;
          // The OpenAI-compatible models close with a usage frame; older
          // `{response}` models send none, so usage stays null for those.
          const u = chunk?.usage;
          if (u) {
            this.usage = {
              provider: this.name,
              model: this.model,
              prompt: u.prompt_tokens ?? 0,
              output: u.completion_tokens ?? 0,
              thoughts: null,
              total: u.total_tokens ?? (u.prompt_tokens ?? 0) + (u.completion_tokens ?? 0),
            };
          }
          const text = chunk?.choices?.[0]?.delta?.content ?? chunk?.response;
          if (text) yield text;
        }
      }
    } finally {
      reader.releaseLock();
    }
  }
}

/**
 * Tries `primary`, and on a PRE-STREAM failure — nothing yielded yet, e.g.
 * Gemini's daily quota exhausted (a 429 RESOURCE_EXHAUSTED that outlives its
 * own connect-retries) — fails over to `secondary`. Once the primary has
 * yielded even one delta we're committed: a mid-stream failure can't be
 * cleanly restarted, so it propagates (the /api/ask route turns that into its
 * extractive last-resort). This is the automatic failover the plain
 * GENERATION_PROVIDER flip deliberately isn't.
 */
class FallbackGeneration implements GenerationProvider {
  /** The member that served the most recent call — what lastUsage() reads. */
  private served: GenerationProvider | null = null;

  constructor(
    private readonly primary: GenerationProvider,
    private readonly secondary: GenerationProvider,
  ) {}

  // Identity and health are the primary's; /api/health probes each member of
  // the chain separately via getGenerationProviderChain().
  get name(): string {
    return this.primary.name;
  }
  get model(): string {
    return this.primary.model;
  }
  ping(signal?: AbortSignal): Promise<void> {
    return this.primary.ping(signal);
  }

  // Usage belongs to whichever member actually produced the last answer, so
  // a failed-over request is costed against the fallback, not the primary.
  lastUsage(): TokenUsage | null {
    return this.served?.lastUsage() ?? null;
  }

  async generate(prompt: string, system?: string): Promise<string> {
    try {
      this.served = this.primary;
      return await this.primary.generate(prompt, system);
    } catch (err) {
      this.served = this.secondary;
      logger.warn("generation.failover", {
        mode: "generate",
        from: this.primary.name,
        to: this.secondary.name,
        err,
      });
      return this.secondary.generate(prompt, system);
    }
  }

  async *generateStream(prompt: string, system?: string): AsyncIterable<string> {
    this.served = this.primary;
    const iterator = this.primary.generateStream(prompt, system)[Symbol.asyncIterator]();
    let started = false;
    try {
      for (;;) {
        const { value, done } = await iterator.next();
        if (done) return;
        started = true;
        yield value;
      }
    } catch (err) {
      if (started) throw err; // committed to the primary — can't fail over mid-stream
      this.served = this.secondary;
      logger.warn("generation.failover", {
        mode: "stream",
        from: this.primary.name,
        to: this.secondary.name,
        err,
      });
      yield* this.secondary.generateStream(prompt, system);
    }
  }
}

function buildProvider(name: string): GenerationProvider {
  if (name === "gemini_flash") {
    const apiKey = process.env.GEMINI_API_KEY;
    const model = process.env.GEMINI_MODEL ?? "gemini-3.6-flash";
    if (!apiKey) {
      throw new Error("GEMINI_API_KEY is required for the gemini_flash generation provider");
    }
    return new GeminiFlashGeneration(apiKey, model);
  }
  if (name === "cloudflare") {
    const accountId = process.env.CLOUDFLARE_ACCOUNT_ID;
    const apiToken = process.env.CLOUDFLARE_API_TOKEN;
    const model = process.env.CLOUDFLARE_GENERATION_MODEL ?? "@cf/meta/llama-3.1-8b-instruct";
    if (!accountId || !apiToken) {
      throw new Error(
        "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required for the cloudflare generation provider",
      );
    }
    return new CloudflareGeneration(accountId, apiToken, model);
  }
  throw new Error(`Unknown generation provider: ${name}`);
}

/**
 * The generation provider for /api/ask. GENERATION_PROVIDER selects the
 * primary (default gemini_flash). GENERATION_FALLBACK_PROVIDER, when set to a
 * different provider, wraps it in automatic mid-request failover (see
 * FallbackGeneration) — e.g. GENERATION_FALLBACK_PROVIDER=cloudflare keeps
 * answers streaming on a free Cloudflare Workers AI model once Gemini's daily
 * quota is spent.
 */
export function getGenerationProvider(): GenerationProvider {
  const [primary, fallback] = getGenerationProviderChain();
  return fallback ? new FallbackGeneration(primary, fallback) : primary;
}

/**
 * The configured providers as a chain — [primary] or [primary, fallback] —
 * without the failover wrapper, so each can be inspected (and pinged by
 * /api/health) on its own. Throws on a missing key, exactly like
 * getGenerationProvider(): a misconfigured fallback is a deploy error, not
 * something to paper over at request time.
 */
export function getGenerationProviderChain(): [GenerationProvider, GenerationProvider | null] {
  const primaryName = process.env.GENERATION_PROVIDER ?? "gemini_flash";
  const primary = buildProvider(primaryName);
  const fallbackName = process.env.GENERATION_FALLBACK_PROVIDER?.trim();
  if (fallbackName && fallbackName !== primaryName) {
    return [primary, buildProvider(fallbackName)];
  }
  return [primary, null];
}
