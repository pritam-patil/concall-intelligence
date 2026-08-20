import { NextResponse } from "next/server";
import { getTableCounts, getProjectRef } from "@/lib/health";

/**
 * GET /api/health — reports the connected Supabase project and the row
 * counts for the tables the app depends on (companies, documents, chunks).
 *
 * This exists so the "web/ points at a project with no data" failure can
 * never be silent again: hit this endpoint (or a monitor does) and you see
 * exactly which project ref is connected and whether it's been migrated and
 * seeded.
 *
 * Status / HTTP code:
 *   - "ok"       (200) — all three tables readable, companies has rows.
 *   - "degraded" (503) — reachable, but companies is empty (schema applied,
 *                        not seeded / not ingested — the selector will be
 *                        blank).
 *   - "error"    (503) — a table couldn't be read (schema not pushed) or the
 *                        client couldn't be constructed (missing env).
 * 503 on anything but "ok" makes it a real health gate for uptime checks,
 * while the JSON body always carries the detail.
 */

export const dynamic = "force-dynamic";

export async function GET() {
  let counts;
  try {
    counts = await getTableCounts();
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { status: "error", project_ref: getProjectRef(), error: message },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  const errored = Object.entries(counts).filter(([, c]) => c.error);
  const status = errored.length
    ? "error"
    : counts.companies.count === 0
      ? "degraded"
      : "ok";

  return NextResponse.json(
    {
      status,
      project_ref: getProjectRef(),
      counts: {
        companies: counts.companies.count,
        documents: counts.documents.count,
        chunks: counts.chunks.count,
      },
      ...(errored.length
        ? { errors: Object.fromEntries(errored.map(([t, c]) => [t, c.error])) }
        : {}),
    },
    {
      status: status === "ok" ? 200 : 503,
      headers: { "Cache-Control": "no-store" },
    },
  );
}
