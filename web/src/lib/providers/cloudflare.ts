/**
 * Shared Cloudflare Workers AI plumbing for the embeddings and generation
 * providers: the `ai/run` URL both call, and a quota-free reachability probe.
 *
 * The probe hits the models catalogue (`ai/models/search`, per_page=1) with
 * the same account id and Bearer token the run endpoint uses. Verified
 * behaviour: 200 + success:true with a valid token; 400 "Authentication
 * failed" (code 9106) with a bad one. It costs no neurons (the free tier's
 * daily budget), so /api/health can call it on every poll.
 *
 * What it deliberately does NOT check: that the configured model id exists.
 * Cloudflare's metadata endpoints don't know every servable id — measured:
 * `@cf/meta/llama-3.1-8b-instruct` is absent from `models/search` and 404s
 * from `models/schema`, yet `ai/run` serves it fine (only the `-fp8` variant
 * is catalogued). A membership check therefore produced a false "degraded"
 * for a working fallback. The only true existence test is a real run, which
 * spends quota — so a retired Workers AI model id surfaces as a generation
 * failure at request time (logged as `generation.failover` /
 * `ask.generation_failed`), not from this probe. Gemini's probe
 * (providers/gemini.ts) does verify the model id, because `models.get` is
 * authoritative there.
 */

export function workersAiRunUrl(accountId: string, model: string): string {
  return `https://api.cloudflare.com/client/v4/accounts/${accountId}/ai/run/${model}`;
}

export async function pingWorkersAi(
  accountId: string,
  apiToken: string,
  signal?: AbortSignal,
): Promise<void> {
  const url = `https://api.cloudflare.com/client/v4/accounts/${accountId}/ai/models/search?per_page=1`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${apiToken}` }, signal });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`Cloudflare API ${res.status}: ${text.replace(/\s+/g, " ").slice(0, 200)}`);
  }
  let data: { success?: boolean; errors?: unknown };
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`Cloudflare API returned non-JSON (${res.status})`);
  }
  if (!data.success) {
    throw new Error(`Cloudflare API error: ${JSON.stringify(data.errors).slice(0, 200)}`);
  }
}
