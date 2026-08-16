#!/usr/bin/env python3
"""Two open questions about NSE access, answered by measurement, not assumption.

  1. PDF ACCESS (the "one unknown"). nsearchives.nseindia.com has served two
     things across this codebase's prior projects — the daily bhavcopy CSV zip
     and the Nifty500 constituent list — never a filing PDF. Whether the same
     nsit-cookie dance that unlocks the JSON API also unlocks PDF downloads
     from that host is untested. This module tests it: pull real
     `attchmntFile` URLs from the live announcements API and download them,
     recording status, size, timing, and any rate-limiting — from this machine
     and (separately, see .github/workflows/probe-nse-access.yml) from GitHub
     Actions egress, because NSE access is measurably location-sensitive (see
     nse-assist/data/upcoming.py's ACCESS QUIRKS note — the same requests that
     work from a residential connection return empty from cloud egress).

  2. SEEDS. Per-symbol full announcement history for the five pilot symbols,
     filtered to rows that look like transcripts or annual reports, as
     candidate ingestion sources for SOURCES.md.

Session/probe pattern lives in ingest.nse_fetch (ingest/src/ingest/nse_fetch.py)
— nse_session()/probe(), ported VERBATIM from nse-assist/data/upcoming.py, now
shared with download.py so the two can't drift apart the way this script's own
copy did the moment nse_fetch.py was written. See that module's docstring for
why each behavior (section-page priming, explicit gzip/deflate, the
empty-body-200 retry) is there.

Only third-party dependency: requests (ingest.nse_fetch has no others either).
This script does not require the ingest package to be installed — it locates
its source directory directly, so `python scripts/probe_nse_access.py ...`
keeps working from a bare `pip install requests` environment, matching how
.github/workflows/probe-nse-access.yml runs it. Every failure mode is
recorded, never worked around — no CAPTCHA solving, no header spoofing beyond
an ordinary browser's, no retry storms against a host that's said no.

    python probe_nse_access.py pdf-access --out results/pdf_access_local.json
    python probe_nse_access.py seeds --out results/seeds.json
    python probe_nse_access.py annual-reports --symbol TCS
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ingest.nse_fetch import (  # noqa: E402
    ANNOUNCEMENTS_REFERER,
    ANNOUNCEMENTS_URL,
    ANNUAL_REPORTS_REFERER,
    ANNUAL_REPORTS_URL,
    REQUEST_TIMEOUT_SECONDS,
    nse_session,
    probe,
)


# --- environment fingerprint, so two JSON files can be told apart at a glance -

def environment_fingerprint():
    is_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    try:
        egress_ip = requests.get("https://api.ipify.org", timeout=10).text.strip()
    except requests.RequestException as exc:
        egress_ip = f"unknown ({exc})"
    return {
        "label": "github-actions" if is_actions else "local",
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "egress_ip": egress_ip,
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }


# --- (1) PDF access -----------------------------------------------------------

PDF_SAMPLE_SYMBOL = "RELIANCE"
PDF_SAMPLE_LOOKBACK_DAYS = 90


def sample_attachment_urls(session, count=10, symbol=PDF_SAMPLE_SYMBOL):
    """Real attchmntFile URLs from the live announcements feed.

    Bounded to the last PDF_SAMPLE_LOOKBACK_DAYS on purpose: an unbounded
    query (see fetch_full_history, used by the seeds probe) returns a
    symbol's ENTIRE history — thousands of rows — and sampling 10 URLs out of
    that would fetch and hold all of it for no reason. 90 days is plenty for
    a handful of recent attachments.
    """
    today = date.today()
    params = {
        "index": "equities",
        "symbol": symbol,
        "from_date": (today - timedelta(days=PDF_SAMPLE_LOOKBACK_DAYS)).strftime("%d-%m-%Y"),
        "to_date": today.strftime("%d-%m-%Y"),
    }
    outcome = probe(session, "announcements-for-pdf-sample", ANNOUNCEMENTS_URL,
                     params, ANNOUNCEMENTS_REFERER)
    if outcome["rows"] is None:
        return outcome, []
    urls = []
    for row in outcome["rows"]:
        url = row.get("attchmntFile")
        if url and url not in urls:
            urls.append(url)
        if len(urls) >= count:
            break
    return outcome, urls


def download_pdf(session, url):
    result = {"url": url, "status": None, "bytes": None, "elapsed_ms": None,
              "content_type": None, "rate_limit_headers": {}, "error": None}
    headers = {"Accept": "application/pdf,*/*", "Accept-Encoding": "gzip, deflate",
               "Referer": ANNOUNCEMENTS_REFERER}
    started = time.monotonic()
    try:
        response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        result["error"] = str(exc)
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        return result
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    result["status"] = response.status_code
    result["bytes"] = len(response.content)
    result["content_type"] = response.headers.get("Content-Type")
    result["rate_limit_headers"] = {
        k: v for k, v in response.headers.items()
        if k.lower() in ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
                          "x-ratelimit-reset")
    }
    if not response.ok:
        result["error"] = f"HTTP {response.status_code}"
    elif result["bytes"] == 0:
        result["error"] = "empty body"
    elif not (response.content[:4] == b"%PDF" or (result["content_type"] or "").startswith("application/pdf")):
        result["error"] = f"not a PDF (content-type={result['content_type']!r}, first bytes={response.content[:16]!r})"
    return result


def run_pdf_access(out_path, count):
    env = environment_fingerprint()
    session = nse_session()
    probe_outcome, urls = sample_attachment_urls(session, count=count)
    print(f"[probe] announcements sample: HTTP {probe_outcome['status']} — "
          f"{len(urls)} attchmntFile URL(s) found "
          f"({probe_outcome['error'] or 'ok'})")

    downloads = []
    for i, url in enumerate(urls, 1):
        # A pause between requests: this is someone else's free infrastructure,
        # and the point is to observe rate-limiting, not to trigger it needlessly.
        if i > 1:
            time.sleep(1.5)
        result = download_pdf(session, url)
        downloads.append(result)
        state = (f"{result['bytes']} bytes in {result['elapsed_ms']}ms"
                  if result["error"] is None else f"FAILED: {result['error']}")
        print(f"[probe] [{i}/{len(urls)}] HTTP {result['status']} {url} — {state}")

    record = {
        "environment": env,
        "announcements_probe": {
            "status": probe_outcome["status"],
            "error": probe_outcome["error"],
            "row_count": len(probe_outcome["rows"]) if probe_outcome["rows"] is not None else None,
        },
        "pdf_downloads": downloads,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2))
    print(f"[probe] wrote {out_path}")
    return record


# --- (2) Seeds -----------------------------------------------------------------

SEED_SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "TATAMOTORS"]
SEED_KEYWORDS = ("transcript", "annual report")


def fetch_full_history(session, symbol):
    """Unbounded query (no from_date/to_date) — NSE answers with the entire
    filing history for the symbol. This is the backfill mechanism, so a
    multi-MB response here is the expected shape, not a bug to work around."""
    return probe(session, f"announcements-full-history-{symbol}", ANNOUNCEMENTS_URL,
                 {"index": "equities", "symbol": symbol}, ANNOUNCEMENTS_REFERER)


def filter_seed_rows(rows):
    matches = []
    for row in rows or []:
        text = f"{row.get('desc', '')} {row.get('attchmntText', '')}".lower()
        if any(keyword in text for keyword in SEED_KEYWORDS):
            matches.append({
                "symbol": row.get("symbol"),
                "desc": row.get("desc"),
                "attchmntText": row.get("attchmntText"),
                "attchmntFile": row.get("attchmntFile"),
                "an_dt": row.get("an_dt"),
                "seq_id": row.get("seq_id"),
            })
    return matches


def run_seeds(out_path, symbols):
    env = environment_fingerprint()
    session = nse_session()
    per_symbol = {}
    for symbol in symbols:
        outcome = fetch_full_history(session, symbol)
        row_count = len(outcome["rows"]) if outcome["rows"] is not None else None
        matches = filter_seed_rows(outcome["rows"])
        per_symbol[symbol] = {
            "probe": {"status": outcome["status"], "error": outcome["error"],
                      "row_count": row_count},
            "matches": matches,
        }
        state = (f"{row_count} row(s), {len(matches)} matching Transcript/Annual Report"
                  if row_count is not None else f"FAILED ({outcome['error']})")
        print(f"[seeds] {symbol}: HTTP {outcome['status']} — {state}")
        time.sleep(1.5)

    record = {"environment": env, "symbols": per_symbol}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2))
    print(f"[seeds] wrote {out_path}")
    return record


# --- annual-reports API, as the dedicated source --------------------------------

def run_annual_reports(symbol):
    session = nse_session()
    outcome = probe(session, "annual-reports", ANNUAL_REPORTS_URL,
                     {"index": "equities", "symbol": symbol}, ANNUAL_REPORTS_REFERER)
    row_count = len(outcome["rows"]) if outcome["rows"] is not None else None
    state = f"{row_count} row(s)" if row_count is not None else f"FAILED ({outcome['error']})"
    print(f"[annual-reports] {symbol}: HTTP {outcome['status']} — {state}")
    if outcome["rows"]:
        print(json.dumps(outcome["rows"][:5], indent=2))
    return outcome


# --- CLI -------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_pdf = sub.add_parser("pdf-access", help="download sample PDFs from nsearchives, record results")
    p_pdf.add_argument("--out", type=Path, default=Path("results/pdf_access.json"))
    p_pdf.add_argument("--count", type=int, default=10)

    p_seeds = sub.add_parser("seeds", help="per-symbol full-history announcements, filtered")
    p_seeds.add_argument("--out", type=Path, default=Path("results/seeds.json"))
    p_seeds.add_argument("--symbols", default=",".join(SEED_SYMBOLS))

    p_ar = sub.add_parser("annual-reports", help="probe the annual-reports API for one symbol")
    p_ar.add_argument("--symbol", default="TCS")

    args = parser.parse_args(argv)
    if args.command == "pdf-access":
        run_pdf_access(args.out, args.count)
    elif args.command == "seeds":
        run_seeds(args.out, [s.strip() for s in args.symbols.split(",") if s.strip()])
    elif args.command == "annual-reports":
        run_annual_reports(args.symbol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
