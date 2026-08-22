import Link from "next/link";
import { getCoverage, type Coverage } from "@/lib/coverage";

// Coverage is read from the database at request time (server-only env), so
// opt out of static prerendering — same reasoning as /chat.
export const dynamic = "force-dynamic";

// Three cross-company starters. Each must be answerable from the ingested
// corpus and name its company so /chat's router auto-scopes it (see
// lib/routing.ts) — an unscoped "what was revenue?" would just prompt the
// visitor to pick a company, which is a bad first impression.
const EXAMPLE_QUESTIONS = [
  "What did Infosys say about its FY2025-26 revenue growth guidance?",
  "How did HDFC Bank management describe net interest margin last quarter?",
  "What are Reliance's capital expenditure plans?",
];

function chatHref(question: string) {
  return `/chat?q=${encodeURIComponent(question)}`;
}

function formatFreshness(iso: string | null): string {
  if (!iso) return "No documents ingested yet.";
  const d = new Date(iso);
  const when = d.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  });
  return `Last updated ${when} (IST).`;
}

export default async function Home() {
  let coverage: Coverage | null = null;
  let loadError: string | null = null;
  try {
    coverage = await getCoverage();
  } catch (err) {
    loadError = err instanceof Error ? err.message : String(err);
  }

  const companies = coverage?.companies ?? [];

  return (
    <main className="mx-auto w-full max-w-3xl px-4 pb-16 pt-12 sm:pt-20">
      {/* Hero */}
      <section className="flex flex-col gap-5">
        <p className="text-xs font-medium uppercase tracking-widest text-zinc-500 dark:text-zinc-400">
          Concall Intelligence
        </p>
        <h1 className="text-balance text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
          Ask questions across Indian companies&rsquo; annual reports and earnings
          calls — with page-level citations.
        </h1>
        <p className="max-w-xl text-pretty text-base text-zinc-600 dark:text-zinc-400">
          Every answer is grounded in passages from the NSE filings themselves. Each
          citation opens the exact page it came from, so you can check the source in
          one click.
        </p>
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Link
            href="/chat"
            className="inline-flex h-10 items-center rounded-xl bg-zinc-900 px-5 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            Start asking
          </Link>
          <span className="text-xs text-zinc-500 dark:text-zinc-400">
            Free · no sign-up
          </span>
        </div>
      </section>

      {/* Example questions */}
      <section className="mt-14">
        <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Try one of these
        </h2>
        <ul className="mt-3 flex flex-col gap-2">
          {EXAMPLE_QUESTIONS.map((q) => (
            <li key={q}>
              <Link
                href={chatHref(q)}
                className="flex items-center justify-between gap-3 rounded-lg border border-black/10 px-4 py-3 text-sm text-zinc-700 transition-colors hover:bg-black/[.03] dark:border-white/15 dark:text-zinc-300 dark:hover:bg-white/[.04]"
              >
                <span>{q}</span>
                <span aria-hidden className="shrink-0 text-zinc-400">
                  →
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      {/* Covered companies */}
      <section className="mt-14">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            Covered companies
            {companies.length > 0 && (
              <span className="ml-2 font-normal text-zinc-500 dark:text-zinc-400">
                {companies.length}
              </span>
            )}
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {loadError ? (
              <span className="text-red-600 dark:text-red-400">
                Couldn&rsquo;t load coverage. {loadError}
              </span>
            ) : (
              <>
                {formatFreshness(coverage?.latestIngestedAt ?? null)} Refreshed
                nightly from NSE.
              </>
            )}
          </p>
        </div>

        {companies.length > 0 && (
          <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
            {companies.map((c) => {
              const parts = [
                c.annualReports > 0 &&
                  `${c.annualReports} annual report${c.annualReports === 1 ? "" : "s"}`,
                c.concalls > 0 && `${c.concalls} call${c.concalls === 1 ? "" : "s"}`,
              ].filter(Boolean);
              return (
                <li key={c.symbol}>
                  <Link
                    href={chatHref(`What did ${c.name} say about its outlook?`)}
                    title={`Ask about ${c.name}`}
                    className="flex h-full flex-col gap-1 rounded-lg border border-black/10 px-3 py-2.5 transition-colors hover:bg-black/[.03] dark:border-white/15 dark:hover:bg-white/[.04]"
                  >
                    <span className="font-mono text-xs font-semibold tracking-tight">
                      {c.symbol}
                    </span>
                    <span className="line-clamp-2 text-xs leading-snug text-zinc-600 dark:text-zinc-400">
                      {c.name}
                    </span>
                    <span className="mt-auto pt-1 text-[11px] text-zinc-400 dark:text-zinc-500">
                      {parts.length === 0 ? "Ingesting…" : parts.join(" · ")}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Disclaimer */}
      <section className="mt-14 border-t border-black/10 pt-6 text-xs leading-relaxed text-zinc-500 dark:border-white/10 dark:text-zinc-400">
        <p>
          <strong className="font-medium text-zinc-700 dark:text-zinc-300">
            Informational only — not investment advice.
          </strong>{" "}
          Answers are generated by a language model from the cited filings and may be
          incomplete or wrong; always verify against the source page before acting on
          anything. Documents are public disclosures from NSE India; this site is not
          affiliated with NSE or any company listed above.
        </p>
      </section>
    </main>
  );
}
