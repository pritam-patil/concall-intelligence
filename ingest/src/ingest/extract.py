"""Per-page text extraction from filing PDFs, via PyMuPDF.

    uv run python -m ingest.extract --pdf path/to.pdf --document-id <uuid> --out rows.jsonl

Emits one JSONL row per page: {"document_id": ..., "page": N, "text": "..."}.
`page` is 1-indexed, matching how a PDF viewer numbers pages (and the
`chunks.page` column in supabase/migrations — see that table's comment).

TWO-COLUMN LAYOUTS. PyMuPDF's raw block order does not reliably follow
visual reading order: on a two-column page the blocks commonly interleave
left- and right-column text mid-sentence, because block order in the
content stream reflects drawing order, not layout. `_order_blocks()`
detects a clean two-column split — no block crosses the horizontal midline,
and both halves have a meaningful number of blocks — and when found, orders
every left-column block top-to-bottom, then every right-column block
top-to-bottom. Anything else (single-column pages, tables, title pages,
a stray block that straddles the middle) falls back to a plain top-to-
bottom sort. Verified against a real two-column annual-report page — see
tests/fixtures/annual_report_page.pdf and tests/test_extract.py.

HEADERS/FOOTERS are stripped heuristically — this is explicitly not
perfect, and false positives/negatives are expected on layouts unlike the
two fixtures this was checked against:

  1. A block within MARGIN_BAND_POINTS of the top or bottom edge, AND short
     (at most MAX_SHORT_BLOCK_WORDS words) — a page number, a short running
     title — is dropped outright, on a single page, no corroboration
     needed. A point margin, not a fraction of page height, is deliberate:
     on the real annual-report fixture, an 8%-of-height band caught a
     genuine section subheading sitting close to the top of the page
     ("Performance overview", 2 words) as a false positive; a page's
     physical margin is roughly constant regardless of page size, so a
     fixed-point band tracks it far better.
  2. A block in that same margin band that ISN'T short is only dropped if
     its digit-normalized text (see _normalize_for_repeat_check) repeats
     across two or more pages of the same document — the signature of a
     running disclaimer/footer, as opposed to a longer heading that
     happens to sit near a margin on one page only. This signal needs
     multiple pages; a single-page document only ever gets rule 1.

Nothing here reads a PDF's outline/bookmarks or font-size-based heading
detection — block position and repetition are the only signals, on
purpose: they're the two that generalize across the very different layouts
an annual report and a concall transcript actually use.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

MARGIN_BAND_POINTS = 45  # ~0.6in — a typical page margin, not a fraction of page height
MAX_SHORT_BLOCK_WORDS = 12
MIN_COLUMN_BLOCKS = 3  # fewer than this per side isn't confidently "two columns"
CROSS_MIDLINE_SLACK_FRACTION = 0.02  # how much a block may overlap the midline and still not "cross" it


@dataclass
class TextBlock:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def _normalize_for_repeat_check(text: str) -> str:
    """Collapse whitespace and replace digit runs with '#', so 'Page 12 of
    268' and 'Page 13 of 268' — or '12 © X Ltd 2020' and '13 © X Ltd 2020'
    — compare equal. That's what makes a running footer detectable as
    repeated even though the one thing in it that actually changes is the
    page number."""
    text = re.sub(r"\d+", "#", text.strip().lower())
    return re.sub(r"\s+", " ", text)


def _page_blocks(page) -> list[TextBlock]:
    blocks = []
    for x0, y0, x1, y1, text, _block_no, block_type in page.get_text("blocks"):
        if block_type != 0:  # 1 = image block; no text to extract
            continue
        text = text.strip()
        if text:
            blocks.append(TextBlock(x0, y0, x1, y1, text))
    return blocks


def _order_blocks(blocks: list[TextBlock], page_width: float) -> list[TextBlock]:
    """Reading-order sort — two-column-aware. See module docstring."""
    if not blocks:
        return []
    mid = page_width / 2
    slack = page_width * CROSS_MIDLINE_SLACK_FRACTION
    crosses_midline = any(b.x0 < mid - slack and b.x1 > mid + slack for b in blocks)

    if not crosses_midline:
        left = [b for b in blocks if (b.x0 + b.x1) / 2 < mid]
        right = [b for b in blocks if (b.x0 + b.x1) / 2 >= mid]
        if len(left) >= MIN_COLUMN_BLOCKS and len(right) >= MIN_COLUMN_BLOCKS:
            return sorted(left, key=lambda b: b.y0) + sorted(right, key=lambda b: b.y0)

    return sorted(blocks, key=lambda b: (round(b.y0), b.x0))


def _in_margin_band(block: TextBlock, page_height: float) -> bool:
    return block.y1 <= MARGIN_BAND_POINTS or block.y0 >= page_height - MARGIN_BAND_POINTS


def extract_pdf(path, document_id) -> list[dict]:
    """Every page of `path`, as JSONL-ready row dicts. Opens and closes its
    own pymupdf.Document — callers pass a path (or anything pymupdf.open()
    accepts), not an already-open document."""
    doc = pymupdf.open(path)
    try:
        return _extract_open_doc(doc, document_id)
    finally:
        doc.close()


def _extract_open_doc(doc, document_id) -> list[dict]:
    pages = [
        (page.number, _order_blocks(_page_blocks(page), page.rect.width), page.rect.height)
        for page in doc
    ]

    # Cross-page repetition counts (rule 2) — over margin-band blocks only,
    # counted at most once per page so a footer repeated twice on one page
    # (unlikely, but not impossible) doesn't inflate its count.
    repeat_counts: Counter[str] = Counter()
    for _, ordered, height in pages:
        seen_this_page = set()
        for b in ordered:
            if _in_margin_band(b, height):
                key = _normalize_for_repeat_check(b.text)
                if key not in seen_this_page:
                    repeat_counts[key] += 1
                    seen_this_page.add(key)

    rows = []
    for page_number, ordered, height in pages:
        kept = []
        for b in ordered:
            if _in_margin_band(b, height):
                if b.word_count <= MAX_SHORT_BLOCK_WORDS:
                    continue  # rule 1
                if repeat_counts[_normalize_for_repeat_check(b.text)] >= 2:
                    continue  # rule 2
            kept.append(b.text)
        # Blocks joined with a BLANK line, not a single "\n": PyMuPDF block
        # text often contains its own internal line-wraps (a bullet that
        # wraps across three lines is still one block, one string, with
        # "\n" between its own visual lines). A single "\n" between blocks
        # would make an in-block line-wrap indistinguishable from an actual
        # block boundary — chunk.py's heading detection depends on being
        # able to tell them apart (see its module docstring).
        rows.append(
            {"document_id": document_id, "page": page_number + 1, "text": "\n\n".join(kept)}
        )
    return rows


# --- extraction-quality flagging (edge-case surfacing) ------------------------

# A page with fewer real characters than this extracted almost no text — an
# image/scanned page, a full-page chart, or a cover. For a text filing that's an
# extraction FAILURE worth logging, not a normal page.
MIN_PAGE_CHARS = 20
# PyMuPDF emits "(cid:NNN)" when a font carries no unicode cmap, and U+FFFD for
# undecodable bytes — both are garbled text, not content.
_CID_RE = re.compile(r"\(cid:\d+\)")
_REPLACEMENT_CHAR = "�"
MAX_GARBLE_RATIO = 0.02  # >2% replacement chars = encoding garbage


def flag_low_quality_pages(rows: list[dict]) -> list[dict]:
    """Pages whose extracted text looks like an extraction FAILURE, not content:
    near-empty (image/scan) or font/encoding garbage (cid/replacement chars).

    Deliberately conservative — legitimate dense tables (low alphabetic ratio but
    real numbers) are NOT flagged, only clear failures — so the edge-case log
    stays precise. Returns {page, reason, detail} dicts for the caller to log
    against the source PDF.
    """
    flagged: list[dict] = []
    for row in rows:
        text = row["text"]
        stripped = "".join(text.split())
        n = len(stripped)
        if n < MIN_PAGE_CHARS:
            flagged.append(
                {"page": row["page"], "reason": "near-empty (image/scan/chart page)", "detail": f"{n} chars"}
            )
            continue
        cid = len(_CID_RE.findall(text))
        garble = text.count(_REPLACEMENT_CHAR)
        if cid or garble / n > MAX_GARBLE_RATIO:
            flagged.append(
                {
                    "page": row["page"],
                    "reason": "font/encoding garbage (cid or replacement chars)",
                    "detail": f"{cid} cid marker(s), {garble} replacement char(s)",
                }
            )
    return flagged


# A real transcript or annual report runs to many thousands of characters. A
# "transcript" that extracts to almost nothing is a whole-document failure the
# per-page check misses: a 1-page Reg-30 intimation ("transcript available on
# our website") filed in place of the transcript, or a scanned/image PDF whose
# lone text page carries only a letterhead.
MIN_DOC_CHARS = 2000


def flag_thin_extraction(rows: list[dict]) -> dict | None:
    """Doc-level flag: the whole document extracted to too little text to be the
    real thing. Returns a {reason, detail} dict or None. Complements the
    per-page flag_low_quality_pages (a 1-page intimation has real text on that
    page, so it isn't near-empty — but the DOCUMENT is still empty of content)."""
    total = sum(len("".join(row["text"].split())) for row in rows)
    if total < MIN_DOC_CHARS:
        return {
            "reason": "thin extraction (little/no body text — likely an intimation notice "
            "or scanned images filed in place of the document)",
            "detail": f"{len(rows)} page(s), {total} chars total",
        }
    return None


def write_jsonl(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = extract_pdf(args.pdf, args.document_id)
    write_jsonl(rows, args.out)
    print(f"[extract] {args.pdf}: {len(rows)} page(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
