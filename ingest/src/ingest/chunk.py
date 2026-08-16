"""Splits one page's text (ingest.extract's output) into overlapping,
sentence-respecting chunks for embedding.

    uv run python -m ingest.chunk --in pages.jsonl --out chunks.jsonl

Reads {document_id, page, text} rows (extract.py's JSONL shape) and emits
{id, document_id, page, section, content, token_count} rows — everything
`chunks` (supabase/migrations) needs except `embedding`, added later by a
separate embed step. `id` is a deterministic sha256 (see chunk_id below),
not the DB's `uuid` default — reconciling the two is the storage step's
problem, not this module's; what matters here is that re-chunking the same
page twice produces byte-identical ids, so a store step can treat this as
an idempotency key (e.g. ON CONFLICT on a (document_id, page, id) index)
without this module knowing anything about how storage does that.

TOKEN COUNTING is whitespace-separated word count, not either embeddings
provider's real tokenizer (BGE's WordPiece would split subwords
differently, and pulling in a real tokenizer — tiktoken's included, its
vocab files are fetched from a CDN on first use — is unwarranted for a
sizing heuristic that only has to hit "~800", not an exact limit). See
count_tokens().

CHUNKING never splits a sentence across a chunk boundary UNLESS a single
sentence alone exceeds the target size — at that point there is no
non-mid-sentence option left, and the chunk is allowed to run long rather
than truncate the sentence. Sentences themselves come from a small regex +
abbreviation-list splitter (see split_sentences) — not a statistical
model, and not perfect on prose it hasn't been checked against.

OVERLAP is achieved by re-including whichever trailing sentences of a
chunk sum to at least OVERLAP_TOKENS, as the start of the next chunk — so
the overlap is always a whole number of complete sentences, for the same
reason chunk boundaries are.

SECTION DETECTION is a text-only heuristic: a line — really a whole
PyMuPDF block, since extract.py joins blocks with a blank line
specifically so this module can tell a block boundary from an in-block
line-wrap (see extract.py's comment on that join) — counts as a heading
candidate if it's short, doesn't end in sentence-terminal punctuation, and
isn't a bullet. Every chunk gets the nearest such heading at or before its
start offset, or None if none precedes it. This has no idea whether a
short standalone line is really a section title or, say, a pull-quote or
graphic caption that happens to look the same in plain text — "nearest
heading if detectable" is a real ceiling, not modesty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

TARGET_CHUNK_TOKENS = 800
OVERLAP_TOKENS = 100
HEADING_MAX_WORDS = 10

_TOKEN_RE = re.compile(r"\S+")


def count_tokens(text: str) -> int:
    """Approximate token count — see module docstring for why this is a
    plain word count rather than a real tokenizer."""
    return len(_TOKEN_RE.findall(text))


# --- sentence splitting -----------------------------------------------------

_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "sh", "smt", "prof", "sr", "jr", "st",
        "ltd", "inc", "co", "corp", "govt", "no", "fig", "approx", "vs",
        "etc", "eg", "ie", "rs", "us", "uk", "fy", "q1", "q2", "q3", "q4",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
        "oct", "nov", "dec",
    }
)  # fmt: skip

# A candidate boundary is a run of .!? followed by whitespace+capital/quote/
# paren, or by end-of-string — NOT by a digit (guards "Rs.54,000", "3.14")
# or a lowercase letter (guards a stray mid-word period from bad extraction).
_CANDIDATE_BOUNDARY_RE = re.compile(r"[.!?]+(?=\s+[A-Z\"'(]|\s*$)")
_WORD_BEFORE_RE = re.compile(r"(\w+)[.!?]*$")


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """[(start, end, sentence_text)], offsets into `text`. A regex boundary
    detector plus a small abbreviation guard — not a statistical sentence
    tokenizer (spaCy/NLTK punkt), which is a heavier dependency than a
    chunk-sizing heuristic warrants. See module docstring."""
    sentences = []
    start = 0
    for m in _CANDIDATE_BOUNDARY_RE.finditer(text):
        end = m.end()
        word_match = _WORD_BEFORE_RE.search(text[start:end])
        preceding_word = word_match.group(1).lower() if word_match else ""
        if preceding_word in _ABBREVIATIONS:
            continue
        chunk = text[start:end].strip()
        if chunk:
            sentences.append((start, end, chunk))
        start = end
    tail = text[start:].strip()
    if tail:
        tail_start = text.rindex(tail, start)
        sentences.append((tail_start, tail_start + len(tail), tail))
    return sentences


# --- section (heading) detection ---------------------------------------------


def _looks_like_heading(block: str) -> bool:
    if not block or not any(c.isalpha() for c in block):
        return False
    if block[0] in "•-*\t":
        return False
    if block[-1] in ".!?":
        return False
    words = block.split()
    return 1 <= len(words) <= HEADING_MAX_WORDS


def _detect_headings(text: str) -> list[tuple[int, str]]:
    """[(offset, heading_text)], in ascending offset order. Scans
    blank-line-separated blocks (see module docstring on why: extract.py's
    join makes a block boundary distinguishable from an in-block
    line-wrap, which this depends on)."""
    headings = []
    offset = 0
    for block in text.split("\n\n"):
        stripped = block.strip()
        if _looks_like_heading(stripped):
            headings.append((offset, stripped))
        offset += len(block) + 2  # +2 for the "\n\n" split away
    return headings


def _nearest_heading(headings: list[tuple[int, str]], offset: int) -> str | None:
    section = None
    for h_offset, h_text in headings:
        if h_offset > offset:
            break
        section = h_text
    return section


# --- chunk id -----------------------------------------------------------------


def chunk_id(document_id: str, page: int, offset: int) -> str:
    """sha256("{document_id}|{page}|{offset}"), hex. Deterministic in both
    directions that matter: same (document_id, page, offset) always
    produces the same id, in any process, on any run — and any change to
    document_id, page, OR the sentence-accumulation logic (which is what
    actually determines offset) changes it. That second property is the
    point: an id doesn't silently keep pointing at "the same chunk" if a
    change to this module would have produced different chunk boundaries.
    """
    key = f"{document_id}|{page}|{offset}".encode()
    return hashlib.sha256(key).hexdigest()


# --- chunking -------------------------------------------------------------------


def chunk_page(
    document_id: str,
    page: int,
    text: str,
    *,
    target_tokens: int = TARGET_CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[dict]:
    """One page's text -> chunk rows. Returns [] for blank/whitespace-only
    text (nothing to chunk, not an error)."""
    sentences = split_sentences(text)
    if not sentences:
        return []
    headings = _detect_headings(text)

    rows = []
    i, n = 0, len(sentences)
    while i < n:
        # Accumulate sentences from i until adding one more would exceed
        # target_tokens — except the very first sentence of a chunk always
        # gets added regardless of its own size; there is no non-mid-
        # sentence alternative to an oversized single sentence.
        acc_tokens = 0
        j = i
        while j < n:
            s_tokens = count_tokens(sentences[j][2])
            if j > i and acc_tokens + s_tokens > target_tokens:
                break
            acc_tokens += s_tokens
            j += 1

        start_offset, end_offset = sentences[i][0], sentences[j - 1][1]
        content = text[start_offset:end_offset].strip()
        rows.append(
            {
                "id": chunk_id(document_id, page, start_offset),
                "document_id": document_id,
                "page": page,
                "section": _nearest_heading(headings, start_offset),
                "content": content,
                "token_count": count_tokens(content),
            }
        )

        if j >= n:
            break

        # Overlap: walk backward from the last included sentence (j - 1)
        # until the trailing run sums to >= overlap_tokens; the next chunk
        # starts there, re-including those sentences whole. Always advance
        # at least one sentence past i so the loop can't stall.
        overlap_acc, overlap_start = 0, j
        for k in range(j - 1, i - 1, -1):
            overlap_acc += count_tokens(sentences[k][2])
            overlap_start = k
            if overlap_acc >= overlap_tokens:
                break
        i = max(overlap_start, i + 1)

    return rows


# --- CLI --------------------------------------------------------------------


def chunk_pages_jsonl(in_path: Path) -> list[dict]:
    rows = []
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            page_row = json.loads(line)
            rows.extend(
                chunk_page(page_row["document_id"], page_row["page"], page_row["text"])
            )
    return rows


def write_jsonl(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="in_path", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = chunk_pages_jsonl(args.in_path)
    write_jsonl(rows, args.out)
    print(f"[chunk] {args.in_path}: {len(rows)} chunk(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
