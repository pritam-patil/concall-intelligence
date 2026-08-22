import type { ReactNode } from "react";
import type { Source } from "@/lib/ask";

/**
 * A deliberately tiny Markdown renderer for streamed answers — just what
 * /api/ask's model emits: paragraphs, unordered/ordered lists, inline
 * **bold**, and numbered citation markers like `[3]` or `[3][5]`. No library
 * (keeps the bundle lean), and it builds React elements rather than HTML, so
 * every character of model output is escaped by React — no
 * dangerouslySetInnerHTML, no injection surface.
 *
 * Citation markers: a `[n]` whose n is a valid 1-based index into `sources`
 * (the passages were numbered the same way in the prompt) renders as a
 * clickable marker that calls `onCite(sources[n-1], n)`. Anything else — an
 * out-of-range number, a bracketed non-number — stays literal text, so a
 * stray marker degrades gracefully instead of becoming a dead link.
 *
 * Streaming-safe: bold matches only a *closed* `**…**`, so a half-arrived
 * `**Fin` renders as literal text until its closing marker streams in.
 */

type Block =
  | { type: "p"; text: string }
  | { type: "h"; level: number; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] };

type CiteHandler = (source: Source, number: number) => void;

// Not global: used with .test()/.replace(), where a /g flag would carry
// lastIndex between calls and misfire.
const BULLET = /^\s*[-*•]\s+/;
const ORDERED = /^\s*\d+\.\s+/;
// `#`…`######` headings — the model uses them to split multi-company answers.
const HEADING = /^\s*(#{1,6})\s+(.*)$/;
// Inline: **bold** OR a citation token [n] / [n, m, …].
const INLINE = /\*\*([^*]+?)\*\*|\[(\d+(?:\s*,\s*\d+)*)\]/g;

function CitationMarker({ n, onClick }: { n: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Show source ${n}`}
      className="relative -top-[0.35em] mx-px inline-flex items-center rounded bg-blue-500/10 px-1 align-baseline text-[0.7em] font-semibold leading-tight text-blue-600 hover:bg-blue-500/20 dark:text-blue-400"
    >
      {n}
    </button>
  );
}

function parseInline(text: string, sources?: Source[], onCite?: CiteHandler): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(INLINE)) {
    const idx = m.index ?? 0;
    if (idx > last) nodes.push(text.slice(last, idx));

    if (m[1] !== undefined) {
      // **bold** — content can't contain a `*`, keeping this linear.
      nodes.push(<strong key={key++}>{m[1]}</strong>);
    } else {
      const numbers = m[2].split(",").map((s) => Number.parseInt(s.trim(), 10));
      const allInRange =
        !!sources && !!onCite && numbers.every((n) => n >= 1 && n <= sources.length);
      if (allInRange) {
        for (const n of numbers) {
          const source = sources![n - 1];
          nodes.push(<CitationMarker key={key++} n={n} onClick={() => onCite!(source, n)} />);
        }
      } else {
        nodes.push(m[0]); // not a resolvable citation — keep literal
      }
    }
    last = idx + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function parseBlocks(text: string): Block[] {
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    if (lines[i].trim() === "") {
      i++;
      continue;
    }
    const heading = HEADING.exec(lines[i]);
    if (heading) {
      blocks.push({ type: "h", level: heading[1].length, text: heading[2].trim() });
      i++;
      continue;
    }
    if (BULLET.test(lines[i])) {
      const items: string[] = [];
      while (i < lines.length && BULLET.test(lines[i])) {
        items.push(lines[i].replace(BULLET, ""));
        i++;
      }
      blocks.push({ type: "ul", items });
    } else if (ORDERED.test(lines[i])) {
      const items: string[] = [];
      while (i < lines.length && ORDERED.test(lines[i])) {
        items.push(lines[i].replace(ORDERED, ""));
        i++;
      }
      blocks.push({ type: "ol", items });
    } else {
      const para: string[] = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !HEADING.test(lines[i]) &&
        !BULLET.test(lines[i]) &&
        !ORDERED.test(lines[i])
      ) {
        para.push(lines[i]);
        i++;
      }
      blocks.push({ type: "p", text: para.join("\n") });
    }
  }
  return blocks;
}

export default function Markdown({
  text,
  sources,
  onCite,
}: {
  text: string;
  sources?: Source[];
  onCite?: CiteHandler;
}) {
  const blocks = parseBlocks(text);
  return (
    <div className="space-y-2">
      {blocks.map((block, i) => {
        if (block.type === "h") {
          // Answers sit under the page's h1, so every level renders as an h3;
          // levels 1–2 are just a touch larger than 3+.
          const large = block.level <= 2;
          return (
            <h3
              key={i}
              className={`pt-2 font-semibold text-zinc-900 dark:text-zinc-100 ${
                large ? "text-base" : "text-sm"
              }`}
            >
              {parseInline(block.text, sources, onCite)}
            </h3>
          );
        }
        if (block.type === "ul") {
          return (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {block.items.map((item, j) => (
                <li key={j}>{parseInline(item, sources, onCite)}</li>
              ))}
            </ul>
          );
        }
        if (block.type === "ol") {
          return (
            <ol key={i} className="list-decimal space-y-1 pl-5">
              {block.items.map((item, j) => (
                <li key={j}>{parseInline(item, sources, onCite)}</li>
              ))}
            </ol>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap">
            {parseInline(block.text, sources, onCite)}
          </p>
        );
      })}
    </div>
  );
}
