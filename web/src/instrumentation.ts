/**
 * Next.js instrumentation: `register()` runs once when the server starts.
 *
 * We use it as a dev-only drift alarm. If the connected Supabase project has
 * zero companies — or the `companies` table doesn't exist at all — the
 * company selector renders empty and the app looks broken for a reason that's
 * easy to miss (wrong project, migrations/seed never applied). This makes
 * that loud at startup instead of silent. Production is left alone: a startup
 * DB probe there is neither wanted nor free.
 */
export async function register() {
  // Only the Node.js server runtime can reach the service-role client; skip
  // the edge runtime, and skip production entirely.
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  if (process.env.NODE_ENV === "production") return;

  try {
    const { getTableCounts, getProjectRef } = await import("@/lib/health");
    const counts = await getTableCounts();
    const ref = getProjectRef() ?? "(unknown project)";

    if (counts.companies.error) {
      warn(
        `Supabase project "${ref}": could not read the "companies" table.`,
        `  → ${counts.companies.error}`,
        "The company selector will be EMPTY. Have the migrations + seed been",
        "applied to THIS project? See ARCHITECTURE.md § Canonical data store.",
      );
    } else if ((counts.companies.count ?? 0) === 0) {
      warn(
        `Supabase project "${ref}": the "companies" table has 0 rows.`,
        "The company selector will be EMPTY. Apply supabase/seed.sql to this",
        "project (or run ingestion). See ARCHITECTURE.md § Canonical data store.",
      );
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    warn(
      `Could not check the "companies" table at startup — ${message}`,
      "The company selector may be empty. Check NEXT_PUBLIC_SUPABASE_URL and",
      "SUPABASE_SERVICE_ROLE_KEY, and that the schema is applied to that project.",
    );
  }
}

function warn(...lines: string[]) {
  // Bold yellow header + yellow body — hard to miss in a dev terminal.
  console.warn(
    "\n\x1b[33;1m⚠  concall-intelligence — startup check\x1b[0m\n" +
      lines.map((line) => `\x1b[33m   ${line}\x1b[0m`).join("\n") +
      "\n",
  );
}
