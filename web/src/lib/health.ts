import { getServiceRoleClient } from "./supabase";

/**
 * Row-count probes for the core tables, used by both /api/health and the
 * dev startup drift-check (instrumentation.ts). Kept in one place so there's
 * a single definition of "how we count" and which tables matter.
 *
 * A per-table `error` (rather than throwing) means one missing table — e.g.
 * the schema was never pushed to this project — still lets the others report,
 * which is exactly the signal we want to surface.
 */

export type TableCount = { count: number | null; error: string | null };

export type TableCounts = {
  companies: TableCount;
  documents: TableCount;
  chunks: TableCount;
};

const TABLES = ["companies", "documents", "chunks"] as const;

export async function getTableCounts(): Promise<TableCounts> {
  const supabase = getServiceRoleClient();
  const entries = await Promise.all(
    TABLES.map(async (table) => {
      // NOT head:true. A HEAD request has no response body, so PostgREST's
      // "table missing" error (PGRST205) comes back as a bare non-2xx that
      // supabase-js surfaces as {count:null, error:null} — indistinguishable
      // from an empty table, which would make this guard report "ok" for a
      // schema that was never applied. A GET with count:"exact" and limit(1)
      // returns the true total in Content-Range, transfers at most one row,
      // and surfaces the missing-table error in `error`.
      const { count, error } = await supabase
        .from(table)
        .select("*", { count: "exact" })
        .limit(1);
      if (error) return [table, { count: null, error: error.message }] as const;
      // An existing table (even empty) returns a numeric count; null here
      // means "no usable count came back" — treat it as unreadable, never 0.
      if (count === null)
        return [table, { count: null, error: "row count unavailable" }] as const;
      return [table, { count, error: null }] as const;
    }),
  );
  return Object.fromEntries(entries) as TableCounts;
}

/**
 * The Supabase project ref (the URL subdomain) the app is pointed at — so a
 * health check or a startup warning can say *which* project it's talking
 * about. This is the whole point: drift is "web/ and ingest/ disagree on
 * which project is canonical," and you can't diagnose that without the ref.
 */
export function getProjectRef(): string | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!url) return null;
  try {
    return new URL(url).hostname.split(".")[0];
  } catch {
    return null;
  }
}
