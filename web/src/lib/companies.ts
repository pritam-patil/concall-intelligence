import { getServiceRoleClient } from "./supabase";

/**
 * The subset of a `companies` row the UI needs to populate the selector.
 * `isin` is intentionally omitted — the selector shows symbol + name only.
 * See supabase/migrations/*_init_schema.sql (the table) and
 * supabase/seed.sql (the seeded ingestion universe).
 */
export type Company = {
  symbol: string;
  name: string;
  industry: string | null;
};

/**
 * Fetch the ingestion universe (the `companies` table) for the company
 * selector, ordered by display name.
 *
 * Server-only: uses the service-role Supabase client (bypasses RLS, reads a
 * server-only key). Call it from a Server Component or Route Handler and pass
 * the result to Client Components as props — never import this from a
 * "use client" module (a bare `import type { Company }` is fine, it's erased
 * at compile time).
 */
export async function getCompanies(): Promise<Company[]> {
  const supabase = getServiceRoleClient();
  const { data, error } = await supabase
    .from("companies")
    .select("symbol, name, industry")
    .order("name");
  if (error) throw new Error(error.message);
  return (data ?? []) as Company[];
}
