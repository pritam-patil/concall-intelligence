import { createClient } from "@supabase/supabase-js";

/**
 * Server-only Supabase client using the service role key (bypasses RLS).
 * Used for the Q&A retrieval query (`match_chunks` RPC over pgvector).
 * Never import this from a Client Component.
 */
export function getServiceRoleClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error("NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set");
  }
  return createClient(url, key, { auth: { persistSession: false } });
}
