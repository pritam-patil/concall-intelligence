/**
 * Embeddings provider interface — server-side only (used to embed the
 * user's question before the pgvector similarity search).
 *
 * Pinned default: Cloudflare Workers AI `@cf/baai/bge-base-en-v1.5`
 * (free tier, 768-dim). Mirrors ingest/src/ingest/providers/embeddings.py —
 * the ingestion pipeline and this app must embed with the same model, or
 * similarity search is meaningless.
 *
 * `gemini` is a fallback, not a second pinned choice — a config flip
 * (EMBEDDINGS_PROVIDER=gemini), not automatic mid-request failover, same
 * as the Python side. First actually exercised for a real reason, not just
 * as a test: Cloudflare's free-tier daily quota (10,000 neurons) ran out
 * from this project's own testing while building /api/search — see
 * ingest/NOTES.md.
 *
 * `ping()` is the quota-free reachability check /api/health runs: network +
 * credentials, plus "does this model id still exist" where the provider's
 * metadata API can answer that (Gemini can; Workers AI can't — see
 * providers/cloudflare.ts), without spending an embedding call.
 */

import { pingWorkersAi, workersAiRunUrl } from "./cloudflare";
import { GEMINI_API_BASE, pingGeminiModel } from "./gemini";

export interface EmbeddingsProvider {
  /** Provider id as spelled in EMBEDDINGS_PROVIDER (e.g. "cloudflare_bge"). */
  readonly name: string;
  readonly model: string;
  readonly dimensions: number;
  embed(texts: string[]): Promise<number[][]>;
  /** Cheap reachability + auth + model-existence probe; rejects with the reason. */
  ping(signal?: AbortSignal): Promise<void>;
}

class CloudflareBgeEmbeddings implements EmbeddingsProvider {
  readonly name = "cloudflare_bge";
  readonly dimensions = 768;
  private readonly url: string;
  private readonly headers: Record<string, string>;

  constructor(
    private readonly accountId: string,
    private readonly apiToken: string,
    readonly model: string,
  ) {
    this.url = workersAiRunUrl(accountId, model);
    this.headers = { Authorization: `Bearer ${apiToken}` };
  }

  async embed(texts: string[]): Promise<number[][]> {
    const res = await fetch(this.url, {
      method: "POST",
      headers: { ...this.headers, "Content-Type": "application/json" },
      body: JSON.stringify({ text: texts }),
    });
    if (!res.ok) {
      throw new Error(`Cloudflare embeddings call failed: ${res.status} ${await res.text()}`);
    }
    const data = await res.json();
    if (!data.success) {
      throw new Error(`Cloudflare embeddings call failed: ${JSON.stringify(data.errors)}`);
    }
    return data.result.data as number[][];
  }

  ping(signal?: AbortSignal): Promise<void> {
    return pingWorkersAi(this.accountId, this.apiToken, signal);
  }
}

class GeminiEmbeddings implements EmbeddingsProvider {
  readonly name = "gemini";
  readonly dimensions = 768;
  private readonly url: string;
  private readonly modelPath: string;

  constructor(
    private readonly apiKey: string,
    readonly model: string,
  ) {
    this.url = `${GEMINI_API_BASE}/models/${model}:batchEmbedContents`;
    this.modelPath = `models/${model}`;
  }

  async embed(texts: string[]): Promise<number[][]> {
    const requests = texts.map((text) => ({
      model: this.modelPath,
      content: { parts: [{ text }] },
      outputDimensionality: this.dimensions,
    }));
    const res = await fetch(`${this.url}?key=${this.apiKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requests }),
    });
    if (!res.ok) {
      throw new Error(`Gemini embeddings call failed: ${res.status} ${await res.text()}`);
    }
    const data = await res.json();
    const vectors: number[][] = data.embeddings.map((e: { values: number[] }) => e.values);
    // Truncated (< native 3072-dim) output isn't unit-normalized — same
    // finding as the Python port (GeminiEmbeddings.embed, ~0.58 observed
    // norm vs. bge's ~1.0); cosine distance is scale-invariant so this
    // wouldn't break ranking either way, but there's no reason to store a
    // vector at a norm that doesn't mean anything.
    return vectors.map((v) => {
      const norm = Math.sqrt(v.reduce((sum, x) => sum + x * x, 0));
      return norm ? v.map((x) => x / norm) : v;
    });
  }

  ping(signal?: AbortSignal): Promise<void> {
    return pingGeminiModel(this.apiKey, this.model, signal);
  }
}

export function getEmbeddingsProvider(): EmbeddingsProvider {
  const provider = process.env.EMBEDDINGS_PROVIDER ?? "cloudflare_bge";
  if (provider === "cloudflare_bge") {
    const accountId = process.env.CLOUDFLARE_ACCOUNT_ID;
    const apiToken = process.env.CLOUDFLARE_API_TOKEN;
    const model = process.env.CLOUDFLARE_EMBEDDINGS_MODEL ?? "@cf/baai/bge-base-en-v1.5";
    if (!accountId || !apiToken) {
      throw new Error(
        "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required for EMBEDDINGS_PROVIDER=cloudflare_bge",
      );
    }
    return new CloudflareBgeEmbeddings(accountId, apiToken, model);
  }
  if (provider === "gemini") {
    const apiKey = process.env.GEMINI_API_KEY;
    const model = process.env.GEMINI_EMBEDDINGS_MODEL ?? "gemini-embedding-001";
    if (!apiKey) {
      throw new Error("GEMINI_API_KEY is required for EMBEDDINGS_PROVIDER=gemini");
    }
    return new GeminiEmbeddings(apiKey, model);
  }
  throw new Error(`Unknown embeddings provider: ${provider}`);
}
