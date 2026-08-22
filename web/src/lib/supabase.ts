import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Server-only Supabase client using the service role key (bypasses RLS).
 * Used for retrieval (the match_chunks_* RPCs), the company/coverage reads,
 * and the rate limiter. Never import this from a Client Component.
 *
 * Serverless / connection-pooling notes (Vercel Functions):
 *
 * - This client talks to Supabase's REST layer (PostgREST) over HTTPS. It
 *   never opens a Postgres connection itself — PostgREST holds a fixed
 *   server-side pool to the database. So the classic serverless failure
 *   mode (N cold function instances × 1 direct connection each, until the
 *   free tier's connection ceiling trips) cannot happen on this path. That
 *   is the reason web/ uses supabase-js + RPCs rather than a `postgres://`
 *   driver, and it's deliberate: DO NOT add a direct database connection
 *   from web/. If one is ever unavoidable, it must go through Supabase's
 *   Supavisor pooler in transaction mode (port 6543, prepared statements
 *   disabled), never the direct port 5432.
 *
 * - One client per isolate. The client is memoised at module scope, so a
 *   warm function instance reuses it (and the process-wide keep-alive HTTP
 *   pool beneath fetch) across invocations instead of rebuilding it per
 *   call. `persistSession`/`autoRefreshToken` are off: there's no user
 *   session, just the service key, and the background refresh timer would
 *   only keep an instance alive for nothing.
 *
 * - Every request is bounded by SUPABASE_REQUEST_TIMEOUT_MS (default 15s).
 *   A stalled upstream otherwise holds the function open for its entire
 *   maxDuration, which on a per-request-billed platform is the expensive
 *   kind of hang. supabase-js surfaces the abort as a normal `{error}`
 *   result, so callers see "TimeoutError: …" rather than a throw.
 */

const REQUEST_TIMEOUT_MS = Number(process.env.SUPABASE_REQUEST_TIMEOUT_MS ?? 15_000);

const fetchWithTimeout: typeof fetch = (input, init) => {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  const signal = init?.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  return fetch(input, { ...init, signal });
};

let cached: { url: string; key: string; client: SupabaseClient } | null = null;

export function getServiceRoleClient(): SupabaseClient {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error("NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set");
  }
  // Keyed on the credentials so a changed env (tests, a rotated key picked up
  // by a new deploy) can never be served a stale client.
  if (cached && cached.url === url && cached.key === key) return cached.client;
  const client = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    global: { fetch: fetchWithTimeout },
  });
  cached = { url, key, client };
  return client;
}
