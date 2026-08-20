"use client";

import { useState } from "react";
import type { Company } from "@/lib/companies";

/**
 * The interactive base of the app: a company selector and the (currently
 * empty) chat area, scoped to whichever company is selected.
 *
 * `companies` is fetched on the server (getCompanies) and passed in as
 * props, so the selector is populated in the first render — no client-side
 * fetch or loading state. The selected `symbol` lives here because it's the
 * shared handle between the selector and the chat: an empty string means
 * "all companies" (the /api/ask `symbol` filter is optional).
 */
export default function ChatShell({
  companies,
  loadError,
}: {
  companies: Company[];
  loadError: string | null;
}) {
  const [symbol, setSymbol] = useState("");
  const selected = companies.find((c) => c.symbol === symbol) ?? null;

  return (
    <>
      <div className="border-b border-black/10 dark:border-white/10">
        <div className="mx-auto w-full max-w-2xl px-4 py-2">
          <label htmlFor="company" className="sr-only">
            Company
          </label>
          <select
            id="company"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            disabled={companies.length === 0}
            className="w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 disabled:opacity-50 dark:border-white/15 dark:focus:border-white/40"
          >
            <option value="">All companies</option>
            {companies.map((c) => (
              <option key={c.symbol} value={c.symbol}>
                {c.symbol} — {c.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center px-4 py-10 text-center">
        {loadError ? (
          <p className="max-w-sm text-sm text-red-600 dark:text-red-400">
            Couldn’t load companies. {loadError}
          </p>
        ) : (
          <p className="max-w-sm text-sm text-zinc-500 dark:text-zinc-400">
            {selected
              ? `Ask a question about ${selected.name}’s NSE filings and earnings calls.`
              : "Select a company, then ask a question about its NSE filings and earnings calls."}
          </p>
        )}
      </div>
    </>
  );
}
