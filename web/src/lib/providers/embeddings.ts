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
 */

export interface EmbeddingsProvider {
  readonly dimensions: number;
  embed(texts: string[]): Promise<number[][]>;
}

class CloudflareBgeEmbeddings implements EmbeddingsProvider {
  readonly dimensions = 768;
  private readonly url: string;
  private readonly headers: Record<string, string>;

  constructor(accountId: string, apiToken: string, model: string) {
    this.url = `https://api.cloudflare.com/client/v4/accounts/${accountId}/ai/run/${model}`;
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
}

class GeminiEmbeddings implements EmbeddingsProvider {
  readonly dimensions = 768;
  private readonly url: string;
  private readonly model: string;
  private readonly apiKey: string;

  constructor(apiKey: string, model: string) {
    this.url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:batchEmbedContents`;
    this.model = `models/${model}`;
    this.apiKey = apiKey;
  }

  async embed(texts: string[]): Promise<number[][]> {
    const requests = texts.map((text) => ({
      model: this.model,
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
