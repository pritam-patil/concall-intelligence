/**
 * Embeddings provider interface — server-side only (used to embed the
 * user's question before the pgvector similarity search).
 *
 * Pinned default: Cloudflare Workers AI `@cf/baai/bge-base-en-v1.5`
 * (free tier, 768-dim). Mirrors ingest/src/ingest/providers/embeddings.py —
 * the ingestion pipeline and this app must embed with the same model, or
 * similarity search is meaningless.
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
  throw new Error(`Unknown embeddings provider: ${provider}`);
}
