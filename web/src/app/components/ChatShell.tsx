"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import type { Company } from "@/lib/companies";
import { streamAsk, type Source } from "@/lib/ask";
import { getSuggestions } from "@/lib/suggestions";
import Markdown from "./Markdown";
import CitationPanel, { type ActiveCitation } from "./CitationPanel";

/**
 * The chat surface: company selector, a client-side-only message history, a
 * streamed answer per question, and a composer. History lives in component
 * state (v1 — no persistence). The selected `symbol` scopes each question
 * (sent as /api/ask's optional `symbol` filter; "" = all companies).
 */

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  refused?: boolean;
  error?: string;
  streaming?: boolean;
};

export default function ChatShell({
  companies,
  loadError,
}: {
  companies: Company[];
  loadError: string | null;
}) {
  const [symbol, setSymbol] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeCitation, setActiveCitation] = useState<ActiveCitation | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const selected = companies.find((c) => c.symbol === symbol) ?? null;
  const disabled = !!loadError;

  // Keep the newest message in view as it streams in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const patch = (id: string, apply: (m: Message) => Partial<Message>) =>
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...apply(m) } : m)),
    );

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy || disabled) return;

    setInput("");
    const userId = crypto.randomUUID();
    const botId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: userId, role: "user", content: q },
      { id: botId, role: "assistant", content: "", streaming: true },
    ]);
    setBusy(true);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamAsk(
        { question: q, symbol: symbol || undefined },
        (event) => {
          if (event.type === "sources") patch(botId, () => ({ sources: event.sources }));
          else if (event.type === "delta") patch(botId, (m) => ({ content: m.content + event.text }));
          else if (event.type === "done") patch(botId, () => ({ streaming: false, refused: event.refused }));
          else if (event.type === "error") patch(botId, () => ({ streaming: false, error: event.error }));
        },
        controller.signal,
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        patch(botId, (m) => ({ streaming: false, error: m.content ? undefined : "Stopped." }));
      } else {
        patch(botId, () => ({ streaming: false, error: err instanceof Error ? err.message : String(err) }));
      }
    } finally {
      patch(botId, (m) => (m.streaming ? { streaming: false } : {}));
      setBusy(false);
      abortRef.current = null;
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <>
      {/* Company selector */}
      <div className="shrink-0 border-b border-black/10 dark:border-white/10">
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

      {/* Messages / empty state */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col items-center justify-center gap-6 px-4 py-10 text-center">
            {loadError ? (
              <p className="max-w-sm text-sm text-red-600 dark:text-red-400">
                Couldn’t load companies. {loadError}
              </p>
            ) : (
              <>
                <p className="max-w-sm text-sm text-zinc-500 dark:text-zinc-400">
                  {selected
                    ? `Ask a question about ${selected.name}’s NSE filings and earnings calls.`
                    : "Select a company, then ask a question about its NSE filings and earnings calls."}
                </p>
                <div className="flex w-full max-w-md flex-col gap-2">
                  {getSuggestions(symbol).map((q) => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => send(q)}
                      className="rounded-lg border border-black/10 px-3 py-2 text-left text-sm text-zinc-700 transition-colors hover:bg-black/[.03] dark:border-white/15 dark:text-zinc-300 dark:hover:bg-white/[.04]"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-2xl flex-col gap-5 px-4 py-5">
            {messages.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-zinc-900 px-3.5 py-2 text-sm text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900">
                    {m.content}
                  </div>
                </div>
              ) : (
                <div key={m.id} className="flex flex-col gap-2">
                  {m.content && (
                    <div
                      className={`text-sm leading-relaxed ${
                        m.refused
                          ? "italic text-zinc-500 dark:text-zinc-400"
                          : "text-zinc-800 dark:text-zinc-200"
                      }`}
                    >
                      <Markdown
                        text={m.content}
                        sources={m.sources}
                        onCite={(source, number) => setActiveCitation({ source, number })}
                      />
                      {m.streaming && (
                        <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-current align-middle" />
                      )}
                    </div>
                  )}
                  {m.streaming && !m.content && (
                    <p className="text-sm text-zinc-400 dark:text-zinc-500">
                      {m.sources?.length
                        ? `Reading ${m.sources.length} passage${m.sources.length === 1 ? "" : "s"}…`
                        : "Searching the filings…"}
                    </p>
                  )}
                  {m.error && (
                    <p className="text-sm text-red-600 dark:text-red-400">⚠ {m.error}</p>
                  )}
                  {m.sources && m.sources.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-0.5">
                      {m.sources.map((s, i) => (
                        <button
                          key={`${m.id}-${i}`}
                          type="button"
                          onClick={() => setActiveCitation({ source: s, number: i + 1 })}
                          className="inline-flex items-center gap-1.5 rounded-full border border-black/10 py-0.5 pl-1 pr-2.5 text-xs text-zinc-600 transition-colors hover:bg-black/[.04] dark:border-white/15 dark:text-zinc-400 dark:hover:bg-white/[.06]"
                          title={s.content.slice(0, 140)}
                        >
                          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-500/10 px-1 text-[0.7em] font-semibold text-blue-600 dark:text-blue-400">
                            {i + 1}
                          </span>
                          {s.symbol} · {s.doc_type} · p.{s.page ?? "?"}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ),
            )}
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="shrink-0 border-t border-black/10 dark:border-white/10">
        <form onSubmit={onSubmit} className="mx-auto flex w-full max-w-2xl items-end gap-2 px-4 py-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            disabled={disabled}
            placeholder={
              selected ? `Ask about ${selected.name}…` : "Ask about any covered company…"
            }
            className="max-h-40 min-h-[2.5rem] flex-1 resize-none rounded-xl border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 disabled:opacity-50 dark:border-white/15 dark:focus:border-white/40"
          />
          {busy ? (
            <button
              type="button"
              onClick={() => abortRef.current?.abort()}
              className="h-10 shrink-0 rounded-xl border border-black/15 px-4 text-sm font-medium transition-colors hover:bg-black/[.04] dark:border-white/15 dark:hover:bg-white/[.06]"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim() || disabled}
              className="h-10 shrink-0 rounded-xl bg-zinc-900 px-4 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              Send
            </button>
          )}
        </form>
        <p className="mx-auto max-w-2xl px-4 pb-2 text-center text-[11px] text-zinc-400 dark:text-zinc-600">
          Answers are grounded in cited filings and may be incomplete. Informational only — not investment advice.
        </p>
      </div>

      <CitationPanel citation={activeCitation} onClose={() => setActiveCitation(null)} />
    </>
  );
}
