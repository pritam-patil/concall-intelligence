"""Period/filing-date derivation (ingest.period).

The cases below are REAL seed rows, not invented strings: the URLs are the
six pilot seeds from SEED_DOCUMENTS, and the announcement prose is the
`attchmntText` the live probe recorded in ingest/results/seeds_local.json.
That matters because the whole point of the module is that NSE's prose is
mostly boilerplate — a test on made-up "quarter ended ..." strings would
prove nothing about the rows this actually has to handle.
"""

from __future__ import annotations

from datetime import date

import pytest

from ingest.period import (
    derive_concall_period,
    filed_at_from_url,
    label_for_quarter_end,
    period_from_filing_date,
    period_from_text,
)

RELIANCE_URL = "https://nsearchives.nseindia.com/corporate/kavinavora_19072026180618_SE_Transcript.pdf"
TCS_URL = "https://nsearchives.nseindia.com/corporate/TCS_CORPCS_15072026193646_SEInt15072026_Signed.pdf"
HDFCBANK_URL = "https://nsearchives.nseindia.com/corporate/HDFCBANK_24072026154746_SEintimationTranscriptofearningscall18jul2026.pdf"
INFY_URL = "https://nsearchives.nseindia.com/corporate/Infosys_28072026202438_SE_filing_Earnings_call_transcript.pdf"
TMCV_URL = "https://nsearchives.nseindia.com/corporate/TMLCOMMERCIAL_19052026145257_NSEBSE.pdf"
TMPV_URL = "https://nsearchives.nseindia.com/corporate/TATAMOTORSSJS_20052026215152_NSEBSETRANSCRIPT.pdf"
INFY_AR_URL = "https://nsearchives.nseindia.com/annual_reports/AR_29313_INFY_2025_2026_U_8985411_30052026200413.pdf"


class TestFiledAtFromUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            # Each expected date is the `an_dt` the announcements probe
            # recorded for that same seq_id (seeds_local.json).
            (RELIANCE_URL, date(2026, 7, 19)),
            (TCS_URL, date(2026, 7, 15)),
            (HDFCBANK_URL, date(2026, 7, 24)),
            (INFY_URL, date(2026, 7, 28)),
            (TMCV_URL, date(2026, 5, 19)),
            (TMPV_URL, date(2026, 5, 20)),
        ],
    )
    def test_matches_the_announcement_date(self, url, expected):
        assert filed_at_from_url(url) == expected

    def test_ignores_the_other_numeric_runs_in_an_annual_report_name(self):
        # AR_29313_INFY_2025_2026_U_8985411_30052026200413 — the stamp is the
        # 14-digit run, not the AR id, the two fiscal years, or the file size.
        assert filed_at_from_url(INFY_AR_URL) == date(2026, 5, 30)

    def test_second_stamp_shaped_run_does_not_win(self):
        # TCS's name repeats a bare ddmmyyyy later (`SEInt15072026`); only the
        # full 14-digit stamp may match.
        assert filed_at_from_url(TCS_URL) == date(2026, 7, 15)

    def test_no_stamp_is_none_not_an_error(self):
        assert filed_at_from_url("https://example.com/transcript.pdf") is None


class TestQuarterFromFilingDate:
    @pytest.mark.parametrize(
        ("filed", "expected"),
        [
            # July filings report the quarter ended 30-Jun — Q1 of FY2026-27.
            (date(2026, 7, 15), "Q1 FY27"),
            (date(2026, 7, 28), "Q1 FY27"),
            # May filings report the quarter ended 31-Mar — Q4 of FY2025-26.
            (date(2026, 5, 19), "Q4 FY26"),
            (date(2026, 5, 20), "Q4 FY26"),
            (date(2026, 11, 4), "Q2 FY27"),
            (date(2027, 2, 10), "Q3 FY27"),
        ],
    )
    def test_maps_to_the_preceding_quarter(self, filed, expected):
        assert period_from_filing_date(filed) == expected

    def test_a_filing_days_after_a_quarter_end_belongs_to_the_previous_one(self):
        # 05-Jul is 5 days after 30-Jun: no company has published Q1 results
        # yet, so this is a late Q4 filing (QUARTER_END_MIN_LAG_DAYS).
        assert period_from_filing_date(date(2026, 7, 5)) == "Q4 FY26"
        assert period_from_filing_date(date(2026, 7, 15)) == "Q1 FY27"

    def test_january_reaches_back_across_the_calendar_year(self):
        # The quarter ended 31-Dec-2026 is Q3 of FY2026-27 — the lookback has
        # to cross the calendar-year boundary to find it.
        assert period_from_filing_date(date(2027, 1, 20)) == "Q3 FY27"


class TestFiscalYearBoundary:
    @pytest.mark.parametrize(
        ("quarter_end", "expected"),
        [
            (date(2026, 6, 30), "Q1 FY27"),
            (date(2026, 9, 30), "Q2 FY27"),
            (date(2026, 12, 31), "Q3 FY27"),
            (date(2026, 3, 31), "Q4 FY26"),
        ],
    )
    def test_april_march_fiscal_year(self, quarter_end, expected):
        assert label_for_quarter_end(quarter_end) == expected


class TestExplicitPeriodInText:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # RELIANCE is the ONE pilot seed whose boilerplate states a period.
            (
                (
                    "Transcript of the discussion on the Unaudited Financial Results "
                    "(Consolidated and Standalone) of the Company for the quarter ended "
                    "June 30, 2026, at the analyst meet held on July 17, 2026"
                ),
                "Q1 FY27",
            ),
            # Filename shapes from the expansion batch (SOURCES.md §2).
            ("SEIntimationQ1FY27EarningsCallTranscriptSigned.pdf", "Q1 FY27"),
            ("IntimationSE20260806EarningsCallTranscriptQ1FY27_Signed.pdf", "Q1 FY27"),
            ("Q3 FY2026-27 earnings call", "Q3 FY27"),
            ("Q2 FY2027 investor call", "Q2 FY27"),
            ("transcript for the quarter ended 31 December 2026", "Q3 FY27"),
        ],
    )
    def test_reads_the_stated_period(self, text, expected):
        assert period_from_text(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            # The boilerplate five: nothing to read, by design.
            "Tata Consultancy Services Limited has informed the Exchange about Transcript",
            "HDFC Bank Limited has informed the Exchange about Transcript",
            "Infosys Limited has informed the Exchange regarding 'Earnings Call Transcript'.",
            # A call DATE in a filename is not a period.
            "SEintimationTranscriptofearningscall18jul2026.pdf",
            # Not a fiscal quarter end — leave it to the filing date.
            "for the quarter ended August 31, 2026",
        ],
    )
    def test_boilerplate_yields_nothing(self, text):
        assert period_from_text(text) is None


class TestDeriveConcallPeriod:
    def test_explicit_text_wins_and_says_so(self):
        period, filed_at, how = derive_concall_period(
            RELIANCE_URL,
            "Transcript of the discussion on the Unaudited Financial Results of the "
            "Company for the quarter ended June 30, 2026",
        )
        assert (period, filed_at, how) == ("Q1 FY27", date(2026, 7, 19), "text")

    def test_the_two_signals_agree_where_both_exist(self):
        # RELIANCE is the only seed that can cross-check the filing-date rule
        # against a period NSE actually stated. They must not disagree.
        assert period_from_filing_date(date(2026, 7, 19)) == "Q1 FY27"

    @pytest.mark.parametrize(
        ("url", "text", "expected"),
        [
            (INFY_URL, "Infosys Limited has informed the Exchange regarding 'Earnings Call Transcript'.", "Q1 FY27"),
            (TCS_URL, "Tata Consultancy Services Limited has informed the Exchange about Transcript", "Q1 FY27"),
            (HDFCBANK_URL, "HDFC Bank Limited has informed the Exchange about Transcript", "Q1 FY27"),
            (TMCV_URL, "Tata Motors Limited has informed the Exchange about Transcript", "Q4 FY26"),
            (TMPV_URL, "Tata Motors Passenger Vehicles Limited has informed the Exchange about Transcript", "Q4 FY26"),
        ],
    )
    def test_boilerplate_rows_fall_back_to_the_filing_date(self, url, text, expected):
        period, filed_at, how = derive_concall_period(url, text)
        assert period == expected
        assert how == "filed_at"
        assert filed_at is not None

    def test_an_undatable_url_derives_nothing_rather_than_guessing(self):
        assert derive_concall_period("https://example.com/t.pdf", "about Transcript") == (None, None, "none")
