"""The curated seed document list from SOURCES.md — what download.py ingests.

Twelve entries: one recent earnings-call transcript (doc_type="concall") and
one FY2025-26 annual report (doc_type="annual_report") per pilot company.
Transcribed by hand from SOURCES.md §2/§3, not re-derived — this is a fixed,
hand-picked sample, not a crawler. If the sample changes, update SOURCES.md
first and this list to match, not the other way around.

nse_seq_id is set for concall entries — NSE's own id for the announcement
filing that carries the transcript, from ingest/results/seeds_local.json and
seeds_tatamotors_successors.json (the raw probe output SOURCES.md §2 was
written from) — and left None for annual_report entries: the
/api/annual-reports feed carries no seq_id at all (SOURCES.md §3, and the
nse_seq_id column comment in the schema migration).

period is left None for concall entries: NSE's own metadata for these rows
is free text ("...for the quarter ended June 30, 2026" for RELIANCE, but
just "...has informed the Exchange about Transcript" for TCS/HDFCBANK/INFY —
see SOURCES.md §2). Reliably parsing a quarter label out of that is not the
kind of thing to guess at for some rows and not others. Annual reports carry
a real structured period (fromYr/toYr) straight from the API, so those get
"FY2025-26".
"""

from __future__ import annotations

SEED_DOCUMENTS = [
    # --- Concall transcripts (SOURCES.md §2) ---------------------------------
    {
        "symbol": "RELIANCE",
        "doc_type": "concall",
        "period": None,
        "source_url": "https://nsearchives.nseindia.com/corporate/kavinavora_19072026180618_SE_Transcript.pdf",
        "nse_seq_id": 106702876,
    },
    {
        "symbol": "TCS",
        "doc_type": "concall",
        "period": None,
        "source_url": "https://nsearchives.nseindia.com/corporate/TCS_CORPCS_15072026193646_SEInt15072026_Signed.pdf",
        "nse_seq_id": 106699212,
    },
    {
        "symbol": "HDFCBANK",
        "doc_type": "concall",
        "period": None,
        "source_url": "https://nsearchives.nseindia.com/corporate/HDFCBANK_24072026154746_SEintimationTranscriptofearningscall18jul2026.pdf",
        "nse_seq_id": 106709751,
    },
    {
        "symbol": "INFY",
        "doc_type": "concall",
        "period": None,
        "source_url": "https://nsearchives.nseindia.com/corporate/Infosys_28072026202438_SE_filing_Earnings_call_transcript.pdf",
        "nse_seq_id": 106714324,
    },
    {
        "symbol": "TMCV",
        "doc_type": "concall",
        "period": None,
        "source_url": "https://nsearchives.nseindia.com/corporate/TMLCOMMERCIAL_19052026145257_NSEBSE.pdf",
        "nse_seq_id": 106629340,
    },
    {
        "symbol": "TMPV",
        "doc_type": "concall",
        "period": None,
        "source_url": "https://nsearchives.nseindia.com/corporate/TATAMOTORSSJS_20052026215152_NSEBSETRANSCRIPT.pdf",
        "nse_seq_id": 106632516,
    },
    # --- Annual reports (SOURCES.md §3), FY2025-26, no seq_id on this feed ---
    {
        "symbol": "RELIANCE",
        "doc_type": "annual_report",
        "period": "FY2025-26",
        "source_url": "https://nsearchives.nseindia.com/annual_reports/AR_29285_RELIANCE_2025_2026_A_11007429_28052026133947.pdf",
        "nse_seq_id": None,
    },
    {
        "symbol": "TCS",
        "doc_type": "annual_report",
        "period": "FY2025-26",
        "source_url": "https://nsearchives.nseindia.com/annual_reports/AR_29263_TCS_2025_2026_A_17427580_15052026234830.pdf",
        "nse_seq_id": None,
    },
    {
        "symbol": "HDFCBANK",
        "doc_type": "annual_report",
        "period": "FY2025-26",
        "source_url": "https://nsearchives.nseindia.com/annual_reports/AR_29735_HDFCBANK_2025_2026_A_12667766_11072026001055.pdf",
        "nse_seq_id": None,
    },
    {
        "symbol": "INFY",
        "doc_type": "annual_report",
        "period": "FY2025-26",
        "source_url": "https://nsearchives.nseindia.com/annual_reports/AR_29313_INFY_2025_2026_U_8985411_30052026200413.pdf",
        "nse_seq_id": None,
    },
    {
        "symbol": "TMCV",
        "doc_type": "annual_report",
        "period": "FY2025-26",
        "source_url": "https://nsearchives.nseindia.com/annual_reports/AR_29361_TMCV_2025_2026_A_20847723_06062026213355.pdf",
        "nse_seq_id": None,
    },
    {
        "symbol": "TMPV",
        "doc_type": "annual_report",
        "period": "FY2025-26",
        "source_url": "https://nsearchives.nseindia.com/annual_reports/AR_29395_TMPV_2025_2026_A_21445169_15062026215933.pdf",
        "nse_seq_id": None,
    },
]
