"""Fixture tests for ingest.extract.

tests/fixtures/annual_report_page.pdf and transcript_page.pdf are each a
single real page, cut from documents actually in ingest/seeds.py — not
synthesized — via pymupdf itself (new_doc.insert_pdf(src, from_page=N,
to_page=N)):

  - annual_report_page.pdf: page 37 (1-indexed) of INFY's FY2025-26 annual
    report — "Awards and recognitions" / "Performance overview". Chosen
    because it is a genuine two-column bullet list (24 left blocks, 49
    right, confirmed via a full-document block-layout scan) with a
    combined title+page-number footer, not a synthetic layout built to
    flatter the extractor.
  - transcript_page.pdf: page 3 (1-indexed) of RELIANCE's Q1 FY27 concall
    transcript — the start of the prepared remarks ("Sh V Srikanth ...
    (Group Performance)"). Clean single column, plus a real repeating-
    style footer ("N \n© Reliance Industries Limited 2020").

Both are cut to one page specifically so `page` in the emitted row is
trivially checkable (must be 1) — a wrong 0-vs-1-indexing bug would fail
that assertion immediately.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from ingest.extract import (
    MAX_SHORT_BLOCK_WORDS,
    TextBlock,
    _in_margin_band,
    _normalize_for_repeat_check,
    _order_blocks,
    extract_pdf,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _text_for(rows: list[dict], page: int) -> str:
    matches = [r["text"] for r in rows if r["page"] == page]
    assert matches, f"no row for page {page} in {[r['page'] for r in rows]}"
    return matches[0]


# --- fixture tests (the required ones) ----------------------------------------


def test_annual_report_two_column_page():
    rows = extract_pdf(FIXTURES_DIR / "annual_report_page.pdf", document_id="doc-ar-1")

    assert len(rows) == 1
    assert rows[0]["document_id"] == "doc-ar-1"
    assert rows[0]["page"] == 1

    text = rows[0]["text"]
    # Left-column bullets (the awards list).
    assert "World's Most Ethical" in text or "World’s Most Ethical" in text
    assert "Compliance Leader Verification" in text
    assert "Gold Stevie" in text
    # Right-column content — on the real page this sits well to the right
    # of the bullet list; if column ordering fell back to naive top-to-
    # bottom (interleaving left/right blocks by y-position) this text
    # would still appear, but the LEFT-column phrase-adjacency checks below
    # would not hold, since a right-column block would get spliced into
    # the middle of a left-column bullet at roughly the same height.
    assert "Avtar & Seramount" in text

    # A phrase that spans a line break WITHIN one bullet's block must stay
    # contiguous — this is what breaks if column detection fails and text
    # gets sorted by raw y-position instead of grouped by column first.
    assert "Infosys Foundation & Infosys ESG Annual Report FY24" in text
    assert "25 received the Gold Stevie" in text
    # And the two bullets must appear in their original top-to-bottom
    # left-column order, not interleaved with right-column material: the
    # "Ethisphere" bullet leads the "Foundation ... Stevie" bullet.
    assert text.index("recognized as one of the World") < text.index("Gold Stevie")

    # Footer stripped: combined running-title + page-number block, short
    # enough (well under MAX_SHORT_BLOCK_WORDS) to be dropped outright.
    assert "Infosys Integrated Annual Report 2025-26" not in text
    # The bare page number from that same footer block.
    assert "36" not in text.split("\n")


def test_transcript_single_column_page():
    rows = extract_pdf(FIXTURES_DIR / "transcript_page.pdf", document_id="doc-tr-1")

    assert len(rows) == 1
    assert rows[0]["document_id"] == "doc-tr-1"
    assert rows[0]["page"] == 1

    text = rows[0]["text"]
    assert "Sh V Srikanth" in text
    assert "extraordinary quarter" in text
    assert "Group Performance" in text
    assert "Rs.54,000 Crores" in text
    # Prepared remarks read in one unbroken pass, not reshuffled.
    assert text.index("extraordinary quarter") < text.index("Rs.54,000 Crores")

    # Footer stripped: "2 \n© Reliance Industries Limited 2020" is short
    # (6 words) and in the bottom margin band -> dropped outright (rule 1,
    # no cross-page corroboration needed even on this single-page fixture).
    assert "Reliance Industries Limited 2020" not in text


# --- unit tests for the two heuristics fixtures alone can't fully exercise ----
#
# Both fixtures are deliberately ONE page each (see module docstring), so
# neither can exercise rule 2 (a long margin-band block dropped only because
# it repeats across pages) — that needs an actual multi-page document. These
# build one in memory with pymupdf rather than checking in a second, larger
# fixture file just to cover one more branch.


def test_two_column_ordering_on_synthetic_blocks():
    # Three-plus blocks per side (MIN_COLUMN_BLOCKS), input order shuffled
    # to prove ordering does the sorting, not input order.
    blocks = [
        TextBlock(300, 300, 500, 320, "right-2"),
        TextBlock(10, 10, 200, 30, "left-1"),
        TextBlock(300, 10, 500, 30, "right-1"),
        TextBlock(10, 300, 200, 320, "left-2"),
        TextBlock(300, 500, 500, 520, "right-3"),
        TextBlock(10, 500, 200, 520, "left-3"),
    ]
    ordered = [b.text for b in _order_blocks(blocks, page_width=600)]
    assert ordered == ["left-1", "left-2", "left-3", "right-1", "right-2", "right-3"]


def test_two_column_falls_back_below_min_column_blocks():
    # Only 2 blocks per side — below MIN_COLUMN_BLOCKS, so this is NOT
    # confidently two columns even though it looks split; falls back to a
    # plain y-then-x sort instead of forcing a column split on thin evidence.
    blocks = [
        TextBlock(300, 300, 500, 320, "right-2"),
        TextBlock(10, 10, 200, 30, "left-1"),
        TextBlock(300, 10, 500, 30, "right-1"),
        TextBlock(10, 300, 200, 320, "left-2"),
    ]
    ordered = [b.text for b in _order_blocks(blocks, page_width=600)]
    assert ordered == ["left-1", "right-1", "left-2", "right-2"]


def test_single_column_fallback_when_a_block_crosses_the_midline():
    # A block spanning the horizontal midline (e.g. a table or a title) —
    # not a clean two-column page, so the fallback (plain top-to-bottom)
    # sort applies instead of forcing a column split that isn't really there.
    blocks = [
        TextBlock(10, 10, 590, 30, "spans the whole width"),
        TextBlock(10, 50, 200, 70, "second"),
        TextBlock(300, 90, 500, 110, "third"),
    ]
    ordered = [b.text for b in _order_blocks(blocks, page_width=600)]
    assert ordered == ["spans the whole width", "second", "third"]


def test_repeat_normalization_matches_across_changing_page_numbers():
    a = _normalize_for_repeat_check("12 \n© Reliance Industries Limited 2020 \n")
    b = _normalize_for_repeat_check("134 \n© Reliance Industries Limited 2020 \n")
    assert a == b  # digits collapsed to '#' on both sides, including the year


def test_margin_band_is_points_not_a_fraction_of_page_height():
    # The real regression this guards: an 8%-of-height band on the annual
    # report fixture's 792pt-tall page (63pt) would have caught a genuine
    # subheading ("Performance overview", y1=50) as a false positive. A
    # fixed-point band (45pt) must not.
    heading = TextBlock(45, 33, 176, 50, "Performance overview")
    footer = TextBlock(45, 762, 561, 772, "Infosys Integrated Annual Report 2025-26\n36")
    assert _in_margin_band(heading, page_height=792) is False
    assert _in_margin_band(footer, page_height=792) is True
    assert heading.word_count <= MAX_SHORT_BLOCK_WORDS  # short regardless — margin is what saves it


@pytest.mark.parametrize("fixture_name", ["annual_report_page.pdf", "transcript_page.pdf"])
def test_fixtures_are_exactly_one_page(fixture_name):
    # Sanity check on the fixtures themselves, not the extractor — if this
    # fails, the fixture file was regenerated wrong, not that extract.py
    # broke.
    doc = pymupdf.open(FIXTURES_DIR / fixture_name)
    try:
        assert doc.page_count == 1
    finally:
        doc.close()


# --- flag_low_quality_pages (extraction edge-case surfacing) ------------------

from ingest.extract import flag_low_quality_pages


def _rows(*texts):
    return [{"document_id": "d", "page": i + 1, "text": t} for i, t in enumerate(texts)]


def test_flags_near_empty_page():
    flags = flag_low_quality_pages(_rows("real content " * 20, "  \n  "))
    assert [f["page"] for f in flags] == [2]
    assert "near-empty" in flags[0]["reason"]


def test_flags_cid_and_replacement_garbage():
    flags = flag_low_quality_pages(_rows("(cid:12)(cid:9)(cid:44) garbled font output here"))
    assert flags and "font/encoding garbage" in flags[0]["reason"]
    flags2 = flag_low_quality_pages(_rows("���������� ��������� undecodable ���������"))
    assert flags2 and "garbage" in flags2[0]["reason"]


def test_does_not_flag_normal_prose_or_dense_tables():
    prose = "The board recommended a dividend of eleven rupees per equity share for the year."
    table = "1,234 5,678 9,012 3,456 7,890 12,345 67,890 11,223 44,556 78,900 10,111 22,333"
    assert flag_low_quality_pages(_rows(prose, table)) == []


def test_flags_thin_whole_document():
    from ingest.extract import flag_thin_extraction
    # A 1-page intimation ("transcript on our website") — real text, but far too
    # little to be the transcript body.
    intimation = _rows("This is to inform you that the transcript of the earnings call "
                       "is available on the Company's website at www.example.com/investors.")
    flag = flag_thin_extraction(intimation)
    assert flag is not None and "thin extraction" in flag["reason"]
    # A real multi-page transcript is not flagged.
    real = _rows(*(["substantive analyst question and management answer " * 40] * 15))
    assert flag_thin_extraction(real) is None
