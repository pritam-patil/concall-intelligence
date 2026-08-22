/**
 * Shared Gemini (Generative Language API) plumbing: a quota-free
 * reachability probe used by both the embeddings and generation providers.
 *
 * `models.get` returns the model's metadata and consumes no generation or
 * embedding quota — which matters, since the free tier's daily generation
 * cap is small (see ingest/NOTES.md) and /api/health may be polled every
 * minute. Verified behaviour: 200 with a valid key; 400 "API key not valid"
 * with a bad one; 404 for a retired model name (the exact failure that
 * once took /api/ask down when gemini-2.0-flash was withdrawn).
 *
 * The key travels in the query string (that's the API's convention), so
 * error messages are built from the status and body only — never the URL.
 */

export const GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta";

export async function pingGeminiModel(
  apiKey: string,
  model: string,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${GEMINI_API_BASE}/models/${model}?key=${apiKey}`, { signal });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Gemini API ${res.status}: ${text.replace(/\s+/g, " ").slice(0, 200)}`);
  }
}
