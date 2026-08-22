import { getServiceRoleClient } from "./supabase";
import { getEmbeddingsProvider } from "./providers/embeddings";
import { getGenerationProviderChain } from "./providers/generation";
import { stopwatch } from "./log";

/**
 * The probes behind GET /api/health (and the dev startup drift-check in
 * instrumentation.ts, which reuses getTableCounts). Kept in one place so
 * there's a single definition of "how we count", which tables matter, and
 * what "reachable" means for each upstream.
 *
 * Two kinds of check:
 *   - Database: row counts for the core tables via the same service-role
 *     client the app uses. A per-table `error` (rather than throwing) means
 *     one missing table — e.g. the schema was never pushed to this project
 *     — still lets the others report, which is exactly the signal we want.
 *   - Providers: each configured provider's `ping()` — a metadata call that
 *     validates network and credentials (and, where the provider's API can
 *     answer it, the model id — Gemini yes, Workers AI no), and spends NO quota
 *     (an uptime monitor polls this every minute; Gemini's free tier is a
 *     handful of generations a day). See providers/cloudflare.ts and
 *     providers/gemini.ts for what each ping actually hits.
 *
 * Every probe is time-boxed (HEALTH_PROBE_TIMEOUT_MS, default 5s) and run
 * concurrently, so a hung upstream reports as a timeout instead of hanging
 * the health check itself.
 */

const PROBE_TIMEOUT_MS = Number(process.env.HEALTH_PROBE_TIMEOUT_MS ?? 5_000);

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
        .limit(1)
        .abortSignal(AbortSignal.timeout(PROBE_TIMEOUT_MS));
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

function errorMessage(err: unknown): string {
  if (err instanceof Error) {
    // AbortSignal.timeout rejects with a DOMException whose `name` carries
    // the useful part ("TimeoutError"); keep it.
    return err.name && err.name !== "Error" ? `${err.name}: ${err.message}` : err.message;
  }
  return String(err);
}

export type DatabaseProbe = {
  project_ref: string | null;
  ok: boolean;
  latency_ms: number;
  /** Set when the client couldn't be built or the probe itself failed. */
  error: string | null;
  counts: { companies: number | null; documents: number | null; chunks: number | null };
  /** Per-table read errors (a missing table → the schema isn't applied). */
  errors?: Record<string, string>;
};

export async function probeDatabase(): Promise<DatabaseProbe> {
  const done = stopwatch();
  const project_ref = getProjectRef();
  const empty = { companies: null, documents: null, chunks: null };
  let counts: TableCounts;
  try {
    counts = await getTableCounts();
  } catch (err) {
    return { project_ref, ok: false, latency_ms: done(), error: errorMessage(err), counts: empty };
  }
  const errored = Object.entries(counts).filter(([, c]) => c.error);
  return {
    project_ref,
    ok: errored.length === 0,
    latency_ms: done(),
    error: null,
    counts: {
      companies: counts.companies.count,
      documents: counts.documents.count,
      chunks: counts.chunks.count,
    },
    ...(errored.length
      ? { errors: Object.fromEntries(errored.map(([t, c]) => [t, c.error as string])) }
      : {}),
  };
}

export type ProviderProbe = {
  /** Provider id as configured (EMBEDDINGS_PROVIDER / GENERATION_PROVIDER). */
  provider: string;
  model: string | null;
  ok: boolean;
  latency_ms: number;
  error: string | null;
};

export type ProviderProbes = {
  embeddings: ProviderProbe;
  generation: ProviderProbe;
  /** GENERATION_FALLBACK_PROVIDER, or null when no failover is configured. */
  generation_fallback: ProviderProbe | null;
};

type Pingable = { name: string; model: string; ping(signal?: AbortSignal): Promise<void> };

async function probe(provider: Pingable): Promise<ProviderProbe> {
  const done = stopwatch();
  try {
    await provider.ping(AbortSignal.timeout(PROBE_TIMEOUT_MS));
    return { provider: provider.name, model: provider.model, ok: true, latency_ms: done(), error: null };
  } catch (err) {
    return {
      provider: provider.name,
      model: provider.model,
      ok: false,
      latency_ms: done(),
      error: errorMessage(err),
    };
  }
}

/** A probe for a provider that couldn't even be constructed (missing env). */
function unbuildable(provider: string, err: unknown): ProviderProbe {
  return { provider, model: null, ok: false, latency_ms: 0, error: errorMessage(err) };
}

export async function probeProviders(): Promise<ProviderProbes> {
  // Construction reads env and throws on a missing key — report that as the
  // probe's failure rather than letting it take down the whole health check.
  let embeddings: Promise<ProviderProbe>;
  try {
    embeddings = probe(getEmbeddingsProvider());
  } catch (err) {
    embeddings = Promise.resolve(
      unbuildable(process.env.EMBEDDINGS_PROVIDER ?? "cloudflare_bge", err),
    );
  }

  let generation: Promise<ProviderProbe>;
  let fallback: Promise<ProviderProbe | null>;
  try {
    const [primary, secondary] = getGenerationProviderChain();
    generation = probe(primary);
    fallback = secondary ? probe(secondary) : Promise.resolve(null);
  } catch (err) {
    generation = Promise.resolve(
      unbuildable(process.env.GENERATION_PROVIDER ?? "gemini_flash", err),
    );
    fallback = Promise.resolve(null);
  }

  const [e, g, f] = await Promise.all([embeddings, generation, fallback]);
  return { embeddings: e, generation: g, generation_fallback: f };
}
