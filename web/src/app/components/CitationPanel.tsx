"use client";

import { useEffect } from "react";
import { docTypeLabel, filedLabel, type Source } from "@/lib/ask";

export type ActiveCitation = { source: Source; number: number };

/**
 * A right-side drawer showing one cited chunk: its metadata (symbol, doc type,
 * period, page), a link to open the source PDF at that page, and the full
 * chunk text the answer was grounded in. Closes on backdrop click or Escape.
 *
 * The PDF link appends `#page=N` — the PDF Open Parameters fragment that
 * Chrome's built-in viewer, Adobe, and others honour to jump to the page.
 * Viewers that don't support it simply open the PDF at page 1, so it's a safe
 * best-effort deep link.
 */
function pdfHref(source: Source): string {
  return source.page != null ? `${source.source_url}#page=${source.page}` : source.source_url;
}

export default function CitationPanel({
  citation,
  onClose,
}: {
  citation: ActiveCitation | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!citation) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [citation, onClose]);

  if (!citation) return null;
  const { source, number } = citation;
  const period = source.period ?? "n/a";
  const page = source.page ?? "n/a";
  // The filing date is what a reader checks the reporting period against —
  // for concalls the period is derived FROM this date (ingest/period.py), so
  // showing only the label would hide where it came from.
  const filed = filedLabel(source.filed_at);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close source panel"
        onClick={onClose}
        className="absolute inset-0 bg-black/40"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Source ${number}`}
        className="relative flex h-full w-full max-w-md flex-col border-l border-black/10 bg-background shadow-xl dark:border-white/10"
      >
        <div className="flex items-start justify-between gap-3 border-b border-black/10 px-4 py-3 dark:border-white/10">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded bg-blue-500/10 px-1 text-xs font-semibold text-blue-600 dark:text-blue-400">
                {number}
              </span>
              <h2 className="truncate text-sm font-semibold">{source.symbol}</h2>
            </div>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              {docTypeLabel(source.doc_type)} · {period} · p.{page}
              {filed && <> · filed {filed}</>}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 shrink-0 rounded p-1 text-lg leading-none text-zinc-500 hover:bg-black/[.05] dark:text-zinc-400 dark:hover:bg-white/[.06]"
          >
            ✕
          </button>
        </div>

        <div className="border-b border-black/10 px-4 py-3 dark:border-white/10">
          <a
            href={pdfHref(source)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-md border border-black/15 px-3 py-1.5 text-sm font-medium transition-colors hover:bg-black/[.04] dark:border-white/15 dark:hover:bg-white/[.06]"
          >
            Open PDF{source.page != null ? ` at page ${source.page}` : ""}
            <span aria-hidden>↗</span>
          </a>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
            {source.content}
          </p>
        </div>
      </aside>
    </div>
  );
}
