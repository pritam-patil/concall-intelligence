import type { ReactNode } from "react";

/**
 * A deliberately tiny Markdown renderer for streamed answers — just what
 * /api/ask's model actually emits: paragraphs, unordered/ordered lists, and
 * inline **bold**. No library (keeps the bundle lean), and crucially it builds
 * React elements rather than HTML, so every character of model output is
 * escaped by React — no dangerouslySetInnerHTML, no injection surface.
 *
 * Streaming-safe: inline bold matches only a *closed* `**…**`, so a half-
 * arrived `**Fin` renders as literal text until its closing marker streams in.
 */

type Block =
  | { type: "p"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] };

// Not global: these are used with .test()/.replace(), where a /g flag would
// carry lastIndex between calls and misfire.
const BULLET = /^\s*[-*•]\s+/;
const ORDERED = /^\s*\d+\.\s+/;

function parseInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  // Bold content can't contain a `*`, which keeps this linear and avoids
  // matching across adjacent bold spans.
  for (const m of text.matchAll(/\*\*([^*]+?)\*\*/g)) {
    const idx = m.index ?? 0;
    if (idx > last) nodes.push(text.slice(last, idx));
    nodes.push(<strong key={key++}>{m[1]}</strong>);
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

export default function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text);
  return (
    <div className="space-y-2">
      {blocks.map((block, i) => {
        if (block.type === "ul") {
          return (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {block.items.map((item, j) => (
                <li key={j}>{parseInline(item)}</li>
              ))}
            </ul>
          );
        }
        if (block.type === "ol") {
          return (
            <ol key={i} className="list-decimal space-y-1 pl-5">
              {block.items.map((item, j) => (
                <li key={j}>{parseInline(item)}</li>
              ))}
            </ol>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap">
            {parseInline(block.text)}
          </p>
        );
      })}
    </div>
  );
}
