import Link from "next/link";
import type { Metadata } from "next";
import { getCompanies, type Company } from "@/lib/companies";
import ChatShell from "../components/ChatShell";

export const metadata: Metadata = {
  // Composed with the root layout's `%s — Concall Intelligence` template.
  title: "Chat",
};

// The company list is read from the database with the service-role client at
// request time (it depends on server-only env), so opt out of static
// prerendering — otherwise Next would try to bake this page, and its DB call,
// at build time. See ARCHITECTURE.md §4 (the online read path).
export const dynamic = "force-dynamic";

// Deep link: /chat?q=<question> auto-sends the question once on load (the
// landing page's example questions use this). Capped to the composer's soft
// limit so a pasted novel doesn't bypass the textarea's maxLength.
const MAX_DEEP_LINK_CHARS = 1000;

export default async function Chat({ searchParams }: PageProps<"/chat">) {
  const { q } = await searchParams;
  const initialQuestion =
    typeof q === "string" ? q.trim().slice(0, MAX_DEEP_LINK_CHARS) : "";

  let companies: Company[] = [];
  let loadError: string | null = null;
  try {
    companies = await getCompanies();
  } catch (err) {
    loadError = err instanceof Error ? err.message : String(err);
  }

  return (
    // Fixed to the viewport height so the composer stays pinned to the bottom
    // and only the message list scrolls (h-dvh handles mobile browser chrome).
    <div className="flex h-dvh flex-col">
      <header className="shrink-0 border-b border-black/10 dark:border-white/10">
        <div className="mx-auto flex w-full max-w-2xl items-baseline justify-between px-4 py-3">
          <div>
            <h1 className="text-base font-semibold tracking-tight">
              <Link href="/" className="hover:underline">
                Concall Intelligence
              </Link>
            </h1>
            <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
              Informational only — not investment advice.
            </p>
          </div>
          <Link
            href="/"
            className="shrink-0 whitespace-nowrap text-xs text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
          >
            Covered companies
          </Link>
        </div>
      </header>
      <ChatShell companies={companies} loadError={loadError} initialQuestion={initialQuestion} />
    </div>
  );
}
