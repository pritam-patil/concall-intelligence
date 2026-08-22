import { getCompanies, type Company } from "./companies";
import { getServiceRoleClient } from "./supabase";

/**
 * What the landing page shows about the corpus: each covered company with how
 * many annual reports / concall transcripts have actually been ingested, and
 * the most recent ingestion timestamp overall (the "data freshness" line).
 *
 * Server-only (service-role client) — same rule as getCompanies().
 *
 * Only *downloaded* documents count (storage_path set): a row discovered but
 * not yet fetched has no chunks, so it can't back an answer — see the
 * documents.storage_path comment in the init_schema migration.
 */

export type CompanyCoverage = Company & {
  annualReports: number;
  concalls: number;
};

export type Coverage = {
  companies: CompanyCoverage[];
  /** ISO timestamp of the newest ingested document, or null if none. */
  latestIngestedAt: string | null;
  totalDocuments: number;
};

type DocRow = { symbol: string; doc_type: string; ingested_at: string };

export async function getCoverage(): Promise<Coverage> {
  const supabase = getServiceRoleClient();
  const [companies, docs] = await Promise.all([
    getCompanies(),
    supabase
      .from("documents")
      .select("symbol, doc_type, ingested_at")
      .not("storage_path", "is", null)
      .then(({ data, error }) => {
        if (error) throw new Error(error.message);
        return (data ?? []) as DocRow[];
      }),
  ]);

  const bySymbol = new Map<string, { annualReports: number; concalls: number }>();
  let latest: string | null = null;
  for (const d of docs) {
    const entry = bySymbol.get(d.symbol) ?? { annualReports: 0, concalls: 0 };
    if (d.doc_type === "annual_report") entry.annualReports++;
    else if (d.doc_type === "concall") entry.concalls++;
    bySymbol.set(d.symbol, entry);
    if (!latest || Date.parse(d.ingested_at) > Date.parse(latest)) latest = d.ingested_at;
  }

  return {
    companies: companies.map((c) => ({
      ...c,
      ...(bySymbol.get(c.symbol) ?? { annualReports: 0, concalls: 0 }),
    })),
    latestIngestedAt: latest,
    totalDocuments: docs.length,
  };
}
