"""Backfill planning (ingest.backfill.plan_updates) — the decision half,
exercised without a database. Rows below are shaped like real `documents`
rows for the pilot seeds."""

from __future__ import annotations

from ingest.backfill import plan_updates

CONCALL = {
    "id": "1", "symbol": "INFY", "doc_type": "concall", "period": None, "filed_at": None,
    "source_url": "https://nsearchives.nseindia.com/corporate/Infosys_28072026202438_SE_filing_Earnings_call_transcript.pdf",
}
ANNUAL = {
    "id": "2", "symbol": "INFY", "doc_type": "annual_report", "period": "FY2025-26", "filed_at": None,
    "source_url": "https://nsearchives.nseindia.com/annual_reports/AR_29313_INFY_2025_2026_U_8985411_30052026200413.pdf",
}


def test_undated_concall_gets_both_period_and_filed_at():
    (item,) = plan_updates([CONCALL])
    assert item["changes"] == {"period": "Q1 FY27", "filed_at": "2026-07-28"}
    assert item["how"] == "filed_at"


def test_annual_report_keeps_its_authoritative_period():
    # FY2025-26 comes structured from /api/annual-reports — the backfill must
    # date the row without touching the period.
    (item,) = plan_updates([ANNUAL])
    assert item["changes"] == {"filed_at": "2026-05-30"}
    assert "period" not in item["changes"]


def test_already_dated_rows_are_a_no_op():
    done_concall = {**CONCALL, "period": "Q1 FY27", "filed_at": "2026-07-28"}
    done_annual = {**ANNUAL, "filed_at": "2026-05-30"}
    assert plan_updates([done_concall, done_annual]) == []


def test_rerunning_the_plan_is_idempotent():
    (item,) = plan_updates([CONCALL])
    settled = {**CONCALL, **item["changes"]}
    assert plan_updates([settled]) == []


def test_undatable_url_is_skipped_not_guessed():
    assert plan_updates([{**CONCALL, "source_url": "https://example.com/t.pdf"}]) == []
