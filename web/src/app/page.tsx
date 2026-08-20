import { getCompanies, type Company } from "@/lib/companies";
import ChatShell from "./components/ChatShell";

// The company list is read from the database with the service-role client at
// request time (it depends on server-only env), so opt out of static
// prerendering — otherwise Next would try to bake this page, and its DB call,
// at build time. See ARCHITECTURE.md §4 (the online read path).
export const dynamic = "force-dynamic";

export default async function Home() {
  let companies: Company[] = [];
  let loadError: string | null = null;
  try {
    companies = await getCompanies();
  } catch (err) {
    loadError = err instanceof Error ? err.message : String(err);
  }

  return (
    <div className="flex flex-1 flex-col">
      <header className="sticky top-0 z-10 border-b border-black/10 bg-background/90 backdrop-blur dark:border-white/10">
        <div className="mx-auto w-full max-w-2xl px-4 py-3">
          <h1 className="text-base font-semibold tracking-tight">Concall Intelligence</h1>
          <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
            Informational only — not investment advice.
          </p>
        </div>
      </header>
      <ChatShell companies={companies} loadError={loadError} />
    </div>
  );
}
