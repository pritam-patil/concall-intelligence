"""Unit tests for ingest.chunk: chunk sizes, overlap behavior, sentence-
boundary preservation, section detection, and chunk-id determinism.

Sizing/overlap/determinism tests use synthetic text with a known, fixed
token count per sentence — precise and fast, and doesn't depend on any PDF
fixture's actual wording holding still. Section detection is checked both
against a clean synthetic example (unambiguous heading/body separation)
and against the real fixtures shared with test_extract.py, since headings
in real extracted text interact with extract.py's block-join in ways a
synthetic example can gloss over.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from ingest.chunk import (
    OVERLAP_TOKENS,
    TARGET_CHUNK_TOKENS,
    chunk_id,
    chunk_page,
    count_tokens,
    split_sentences,
)
from ingest.extract import extract_pdf

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Fixed-length filler sentence: 19 whitespace-tokens, verified in
# test_filler_sentence_token_count below so a change to the template can't
# silently invalidate every size assertion built on it.
_SENTENCE_TOKENS = 19


def _filler_sentence(i: int) -> str:
    return (
        f"This is filler sentence number {i} with exactly enough words to "
        f"pad things out nicely for testing purposes today."
    )


def _filler_text(n: int) -> str:
    return " ".join(_filler_sentence(i) for i in range(n))


def test_filler_sentence_token_count():
    assert count_tokens(_filler_sentence(0)) == _SENTENCE_TOKENS


# --- chunk sizing --------------------------------------------------------------


def test_chunks_stay_at_or_under_target_tokens():
    text = _filler_text(120)  # ~2280 tokens -> several chunks
    chunks = chunk_page("doc-1", 1, text)
    assert len(chunks) >= 3
    for c in chunks:
        assert c["token_count"] <= TARGET_CHUNK_TOKENS


def test_non_final_chunks_are_close_to_target_not_stopping_early():
    text = _filler_text(120)
    chunks = chunk_page("doc-1", 1, text)
    # Every chunk but the last should be within one sentence's worth of the
    # target -- if packing stopped early (a bug) this would catch a chunk
    # far short of 800 that isn't the last one.
    for c in chunks[:-1]:
        assert c["token_count"] > TARGET_CHUNK_TOKENS - _SENTENCE_TOKENS


def test_single_oversized_sentence_is_not_split():
    # One "sentence" (no terminal punctuation at all, so split_sentences
    # returns it whole) bigger than the target -- allowed to run long
    # rather than cut mid-sentence.
    huge = " ".join(f"word{i}" for i in range(TARGET_CHUNK_TOKENS + 200))
    chunks = chunk_page("doc-1", 1, huge)
    assert len(chunks) == 1
    assert chunks[0]["token_count"] == TARGET_CHUNK_TOKENS + 200
    assert chunks[0]["content"] == huge


def test_empty_and_blank_text_produce_no_chunks():
    assert chunk_page("doc-1", 1, "") == []
    assert chunk_page("doc-1", 1, "   \n\n  ") == []


# --- overlap ----------------------------------------------------------------------


def _sentences_of(chunk_content: str) -> list[str]:
    # The real splitter, not ad hoc string surgery -- content.split(". ")
    # doesn't reliably invert how content was built (text[start:end],
    # stripped), so re-deriving sentences the same way chunk_page did is
    # what makes this comparison actually correct rather than coincidental.
    return [s[2] for s in split_sentences(chunk_content)]


def test_consecutive_chunks_overlap_by_shared_sentences():
    text = _filler_text(120)
    chunks = chunk_page("doc-1", 1, text)
    assert len(chunks) >= 2

    for prev, nxt in pairwise(chunks):
        shared = set(_sentences_of(prev["content"])) & set(_sentences_of(nxt["content"]))
        assert shared, "consecutive chunks should share at least one sentence"

        overlap_tokens = sum(count_tokens(s) for s in shared)
        # Not exact -- the algorithm walks backward by whole sentences
        # until it reaches OVERLAP_TOKENS, so it can overshoot by up to
        # one sentence's worth.
        assert overlap_tokens >= OVERLAP_TOKENS
        assert overlap_tokens < OVERLAP_TOKENS + _SENTENCE_TOKENS * 2


def test_overlap_shared_sentences_are_the_tail_of_prev_and_head_of_next():
    text = _filler_text(120)
    chunks = chunk_page("doc-1", 1, text)
    prev, nxt = chunks[0], chunks[1]

    prev_sentences = _sentences_of(prev["content"])
    next_sentences = _sentences_of(nxt["content"])
    shared = set(prev_sentences) & set(next_sentences)
    n_shared = len(shared)

    assert n_shared > 0
    assert prev_sentences[-n_shared:] == next_sentences[:n_shared]


def test_no_overlap_when_only_one_chunk():
    text = _filler_text(5)  # well under target -- a single chunk
    chunks = chunk_page("doc-1", 1, text)
    assert len(chunks) == 1


# --- never splitting mid-sentence -------------------------------------------------


def test_chunk_boundaries_align_to_sentence_boundaries():
    text = _filler_text(60)
    sentence_texts = {s[2] for s in split_sentences(text)}
    chunks = chunk_page("doc-1", 1, text)
    for c in chunks:
        # Every chunk's content, re-split the same way the chunker itself
        # splits sentences, must be made ENTIRELY of complete sentences
        # from the original text -- none partial, and the same set of
        # sentences whether reached via the whole text or via one chunk's
        # slice of it.
        for piece in _sentences_of(c["content"]):
            assert piece in sentence_texts, f"partial/unknown sentence fragment: {piece!r}"


def test_real_transcript_sentence_stays_whole_across_a_chunk_boundary_region():
    # Regression-flavored: on the real transcript fixture, a distinctive
    # multi-clause sentence must appear intact in whichever chunk it lands
    # in, not truncated at a token-count cutoff.
    rows = extract_pdf(FIXTURES_DIR / "transcript_page.pdf", "doc-tr")
    chunks = chunk_page("doc-tr", 1, rows[0]["text"])
    combined = " ".join(c["content"] for c in chunks)
    assert (
        "our overall EBITDA is more than \nRs.54,000 Crores, so we are up 10%."
        in combined
        or "our overall EBITDA is more than Rs.54,000 Crores, so we are up 10%."
        in combined
    )


# --- section detection --------------------------------------------------------


def test_section_follows_nearest_preceding_heading():
    text = (
        "Intro sentence ends here.\n\n"
        "Section One\n\n"
        + _filler_text(50)
        + "\n\nSection Two\n\n"
        + _filler_text(50)
    )
    chunks = chunk_page("doc-1", 1, text)
    sections = [c["section"] for c in chunks]

    # The intro sentence (before any heading) belongs to no section.
    assert sections[0] is None
    # Every later chunk is attributed to whichever of the two headings
    # precedes it, and "Section One" chunks precede "Section Two" chunks.
    assert "Section One" in sections
    assert "Section Two" in sections
    assert sections.index("Section One") < sections.index("Section Two")


def test_heading_like_bullet_is_not_mistaken_for_a_heading():
    text = "Intro sentence here.\n\n• Short bullet\n\n" + _filler_text(5)
    chunks = chunk_page("doc-1", 1, text)
    assert all(c["section"] != "• Short bullet" for c in chunks)


def test_no_heading_before_first_chunk_is_none():
    chunks = chunk_page("doc-1", 1, _filler_text(5))
    assert chunks[0]["section"] is None


@pytest.mark.parametrize(
    "fixture_name,expected_section_substring",
    [
        ("annual_report_page.pdf", "overview"),  # "Performance overview" (first heading on the page)
        ("transcript_page.pdf", "Transcript"),
    ],
)
def test_real_fixtures_get_a_detected_section(fixture_name, expected_section_substring):
    rows = extract_pdf(FIXTURES_DIR / fixture_name, "doc-1")
    chunks = chunk_page("doc-1", 1, rows[0]["text"])
    assert chunks[0]["section"] is not None
    assert expected_section_substring in chunks[0]["section"]


# --- chunk id determinism -------------------------------------------------------


def test_chunk_id_is_deterministic_across_calls():
    text = _filler_text(80)
    ids_1 = [c["id"] for c in chunk_page("doc-1", 3, text)]
    ids_2 = [c["id"] for c in chunk_page("doc-1", 3, text)]
    assert ids_1 == ids_2
    assert len(ids_1) == len(set(ids_1))  # also distinct from each other


def test_chunk_id_changes_with_document_id():
    text = _filler_text(20)
    id_a = chunk_page("doc-A", 1, text)[0]["id"]
    id_b = chunk_page("doc-B", 1, text)[0]["id"]
    assert id_a != id_b


def test_chunk_id_changes_with_page():
    text = _filler_text(20)
    id_p1 = chunk_page("doc-1", 1, text)[0]["id"]
    id_p2 = chunk_page("doc-1", 2, text)[0]["id"]
    assert id_p1 != id_p2


def test_chunk_id_changes_with_offset():
    # Two chunks of the same page necessarily start at different offsets;
    # their ids must differ even though document_id and page are equal.
    text = _filler_text(120)
    chunks = chunk_page("doc-1", 1, text)
    assert len({c["id"] for c in chunks}) == len(chunks)


def test_chunk_id_matches_the_documented_sha256_format():
    assert chunk_id("doc-1", 3, 42) == chunk_id("doc-1", 3, 42)
    import hashlib

    expected = hashlib.sha256(b"doc-1|3|42").hexdigest()
    assert chunk_id("doc-1", 3, 42) == expected


def test_chunk_id_is_stable_across_process_runs():
    # A fixed expected value, not just "ran twice in one process" (the
    # test above) -- catches a future refactor that changes the hash INPUT
    # FORMAT (delimiter, field order, str() of an int vs the int itself)
    # even if the new format is still internally self-consistent, which
    # calling chunk_id() twice in the same run would never notice.
    assert (
        chunk_id("00000000-0000-0000-0000-000000000000", 1, 0)
        == "34398bf144eb30c6ec41a59354b762ac8f0f0117eb3bf382cf986fa0bf17b95d"
    )
