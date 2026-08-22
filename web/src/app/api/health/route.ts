import { NextResponse } from "next/server";
import { probeDatabase, probeProviders } from "@/lib/health";
import { logger, stopwatch } from "@/lib/log";

/**
 * GET /api/health — is this deployment able to answer a question right now?
 *
 * Probes, concurrently and each time-boxed (see lib/health.ts):
 *   - db:         the connected Supabase project ref + row counts for
 *                 companies / documents / chunks (the drift guard: "web/
 *                 points at a project with no data" can never be silent).
 *   - providers:  a quota-free ping of the embeddings provider, the primary
 *                 generation provider, and the failover generation provider
 *                 (network + credentials + model id, no tokens spent).
 *
 * Status / HTTP code:
 *   - "ok"       (200) — everything reachable, companies has rows.
 *   - "degraded" (503) — still answering, but not at full strength: the
 *                        primary generation provider is unreachable and
 *                        answers are coming from the failover model, or the
 *                        schema is applied but companies is empty (the
 *                        selector will be blank).
 *   - "error"    (503) — questions cannot be answered: a table can't be read
 *                        (schema not pushed), the DB client couldn't be built
 *                        (missing env), the embeddings provider is down, or
 *                        every generation provider is down.
 * 503 on anything but "ok" makes it a real health gate for uptime monitors;
 * `problems` spells out why, and the JSON body always carries the detail.
 *
 * `deployment` echoes what Vercel knows about this build (commit, region,
 * environment) so a report can be matched to exactly what is running.
 */

export const dynamic = "force-dynamic";
// Three probes × a 5s time-box each, in parallel — well inside this.
export const maxDuration = 15;

type Status = "ok" | "degraded" | "error";

export async function GET() {
  const total = stopwatch();
  const [db, providers] = await Promise.all([probeDatabase(), probeProviders()]);

  const problems: string[] = [];
  let status: Status = "ok";
  const degrade = (why: string) => {
    problems.push(why);
    if (status === "ok") status = "degraded";
  };
  const fail = (why: string) => {
    problems.push(why);
    status = "error";
  };

  if (db.error) fail(`database: ${db.error}`);
  else if (db.errors)
    fail(
      `database: unreadable table(s) — ${Object.entries(db.errors)
        .map(([t, e]) => `${t}: ${e}`)
        .join("; ")}`,
    );
  else if (db.counts.companies === 0)
    degrade("database: companies table is empty (seed/ingest not applied)");

  if (!providers.embeddings.ok)
    fail(`embeddings (${providers.embeddings.provider}): ${providers.embeddings.error}`);

  const fb = providers.generation_fallback;
  if (!providers.generation.ok) {
    const why = `generation (${providers.generation.provider}): ${providers.generation.error}`;
    if (fb?.ok) degrade(`${why} — serving from failover ${fb.provider}`);
    else fail(why);
  }
  if (fb && !fb.ok && providers.generation.ok)
    degrade(`generation failover (${fb.provider}): ${fb.error}`);

  const body = {
    status,
    problems,
    checked_at: new Date().toISOString(),
    latency_ms: total(),
    deployment: {
      env: process.env.VERCEL_ENV ?? process.env.NODE_ENV ?? null,
      // CLI deploys set these to "" rather than leaving them unset.
      commit: process.env.VERCEL_GIT_COMMIT_SHA?.slice(0, 7) || null,
      region: process.env.VERCEL_REGION || null,
    },
    db,
    providers,
  };

  if (status !== "ok") {
    logger.warn("health.not_ok", { status, problems, latency_ms: body.latency_ms });
  }

  return NextResponse.json(body, {
    status: status === "ok" ? 200 : 503,
    headers: { "Cache-Control": "no-store" },
  });
}
