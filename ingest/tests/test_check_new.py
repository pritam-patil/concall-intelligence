"""Unit tests for ingest.check_new — the pure, offline parts: the keyword
recipe, doc construction, ledger keys, the seen-ledger round-trip, and the
discover/dedupe logic (with announcements mocked).

Bare-runner discipline (nse-assist's lesson): the ledger PATH is isolated via
the SEEN_LEDGER_PATH override — the exact mechanism CI uses — so these tests
exercise cold-start-with-no-ledger and read/write against a temp path, never
the committed repo file. The live network path (real NSE, real Supabase) is
verified separately by a `--check-for-new` run; mocking it here would test the
mock.
"""

from __future__ import annotations

from datetime import date

import pytest

from ingest import check_new


@pytest.fixture(autouse=True)
def _isolate_ledger(tmp_path, monkeypatch):
    """Point the ledger at a temp file for every test — the same SEEN_LEDGER_PATH
    override CI sets, so no test can touch (or depend on) the committed file."""
    monkeypatch.setenv("SEEN_LEDGER_PATH", str(tmp_path / "seen.json"))


# --- keyword recipe (classify) ------------------------------------------------


def test_classify_transcript_is_concall():
    row = {"desc": "Analysts/Institutional Investor Meet/Con. Call Updates",
           "attchmntText": "Transcript of the earnings call for Q1."}
    assert check_new.classify(row) == "concall"


def test_classify_annual_report():
    row = {"desc": "Annual Report", "attchmntText": "Annual Report 2025-26 attached."}
    assert check_new.classify(row) == "annual_report"


def test_classify_is_case_insensitive():
    assert check_new.classify({"desc": "TRANSCRIPT", "attchmntText": ""}) == "concall"


def test_classify_transcript_wins_over_annual_report():
    row = {"desc": "Transcript", "attchmntText": "also references the annual report"}
    assert check_new.classify(row) == "concall"


def test_classify_ignores_unrelated_rows():
    assert check_new.classify({"desc": "Change in Directors", "attchmntText": "Board note"}) is None


# --- row_to_doc ---------------------------------------------------------------


def test_row_to_doc_builds_seed_shaped_dict():
    row = {"symbol": "RELIANCE", "attchmntFile": "https://nsearchives.nseindia.com/x.pdf",
           "seq_id": "106702876"}
    doc = check_new.row_to_doc(row, "concall")
    assert doc == {
        "symbol": "RELIANCE", "doc_type": "concall", "period": None,
        "source_url": "https://nsearchives.nseindia.com/x.pdf", "nse_seq_id": 106702876,
    }


def test_row_to_doc_none_without_attachment():
    assert check_new.row_to_doc({"symbol": "TCS", "seq_id": "1"}, "concall") is None


def test_row_to_doc_tolerates_missing_or_nonnumeric_seq_id():
    doc = check_new.row_to_doc({"symbol": "TCS", "attchmntFile": "u", "seq_id": None}, "concall")
    assert doc["nse_seq_id"] is None
    doc2 = check_new.row_to_doc({"symbol": "TCS", "attchmntFile": "u", "seq_id": "-"}, "concall")
    assert doc2["nse_seq_id"] is None


# --- ledger_key ---------------------------------------------------------------


def test_ledger_key_prefers_seq_id():
    assert check_new.ledger_key({"symbol": "TCS", "source_url": "u", "nse_seq_id": 42}) == "seq:42"


def test_ledger_key_falls_back_to_symbol_url():
    assert check_new.ledger_key({"symbol": "TCS", "source_url": "u", "nse_seq_id": None}) == "TCS|u"


# --- seen-ledger round-trip (path-isolated) -----------------------------------


def test_load_seen_cold_start_is_empty():
    # No ledger file at the isolated path yet — a valid cold start, not an error.
    assert check_new.load_seen() == set()


def test_save_then_load_roundtrips_as_a_set():
    check_new.save_seen({"seq:2", "seq:1"})
    assert check_new.load_seen() == {"seq:1", "seq:2"}


def test_save_seen_writes_sorted_list(tmp_path):
    check_new.save_seen({"seq:9", "seq:1", "seq:5"})
    import json

    payload = json.loads((tmp_path / "seen.json").read_text())
    assert payload == {"seen": ["seq:1", "seq:5", "seq:9"]}


def test_load_seen_tolerates_corrupt_file(tmp_path):
    (tmp_path / "seen.json").write_text("not json{")
    assert check_new.load_seen() == set()


# --- discover_new (announcements mocked) --------------------------------------


def _outcome(rows):
    return {"name": "x", "status": 200, "rows": rows, "error": None}


def test_discover_new_keeps_only_unseen_matches(monkeypatch):
    rows = [
        {"symbol": "RELIANCE", "desc": "Con. Call", "attchmntText": "Transcript ...",
         "attchmntFile": "https://a/t1.pdf", "seq_id": "111"},
        {"symbol": "RELIANCE", "desc": "Board Meeting", "attchmntText": "outcome",
         "attchmntFile": "https://a/b.pdf", "seq_id": "222"},  # not a transcript/AR -> ignored
        {"symbol": "RELIANCE", "desc": "Annual Report", "attchmntText": "annual report",
         "attchmntFile": "https://a/ar.pdf", "seq_id": "333"},
    ]
    monkeypatch.setattr(check_new, "announcements_for", lambda *a, **k: _outcome(rows))

    docs = check_new.discover_new(None, ["RELIANCE"], date(2026, 8, 1), date(2026, 8, 21), seen={"seq:111"})

    # seq:111 already seen; seq:222 not a match; only seq:333 (annual_report) is new.
    assert [(d["nse_seq_id"], d["doc_type"]) for d in docs] == [(333, "annual_report")]


def test_discover_new_dedupes_within_run(monkeypatch):
    dup = {"symbol": "TCS", "desc": "Transcript", "attchmntText": "transcript",
           "attchmntFile": "https://a/t.pdf", "seq_id": "500"}
    monkeypatch.setattr(check_new, "announcements_for", lambda *a, **k: _outcome([dup, dict(dup)]))
    docs = check_new.discover_new(None, ["TCS"], date(2026, 8, 1), date(2026, 8, 21), seen=set())
    assert len(docs) == 1  # the same seq_id twice collapses to one


def test_discover_new_skips_a_failed_symbol(monkeypatch):
    def fake(session, symbol, *a, **k):
        if symbol == "BROKEN":
            return {"name": "x", "status": None, "rows": None, "error": "empty body"}
        return _outcome([{"symbol": symbol, "desc": "Transcript", "attchmntText": "transcript",
                          "attchmntFile": f"https://a/{symbol}.pdf", "seq_id": "1"}])

    monkeypatch.setattr(check_new, "announcements_for", fake)
    monkeypatch.setattr(check_new.time, "sleep", lambda s: None)  # no real pacing delay in tests
    docs = check_new.discover_new(None, ["BROKEN", "TCS"], date(2026, 8, 1), date(2026, 8, 21), seen=set())
    assert [d["symbol"] for d in docs] == ["TCS"]  # BROKEN skipped, run continued
