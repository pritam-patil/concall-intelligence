#!/usr/bin/env python3
"""Repeatable backfill of CONCALL TRANSCRIPTS for given symbols, in batches.

The nightly check-for-new (ingest.check_new) discovers transcripts AND
annual-report announcements. This narrows to transcripts only, on purpose: the
announcement-keyword annual-report matches are noisy (AGM notices, newspaper
publications, "letter to shareholders" — not the AR PDF), so real annual
reports should come from the dedicated /api/annual-reports endpoint instead —
parked for a buffer burst, see ingest/NOTES.md "Extraction & coverage edge
cases".

Batching: pass --symbol repeatedly (or run per group of ~5) to stay inside the
Cloudflare free-tier daily embedding quota. Partial runs are safe: the ledger
records only fully-embedded docs and download.ingest_one / embed_chunks both
dedupe, so a re-run resumes exactly where a quota-exhausted one stopped
(same logic as ingest.check_new).

    python scripts/backfill_transcripts.py --since-days 150 --symbol ICICIBANK --symbol SBIN
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ingest import check_new, download
from ingest.config import get_settings
from ingest.db import get_client
from ingest.nse_fetch import DOWNLOAD_PACE_SECONDS, nse_session
from ingest.run import ingest_docs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", action="append", dest="symbols", required=True)
    parser.add_argument("--since-days", type=int, default=150)
    args = parser.parse_args(argv)

    client = get_client(get_settings())
    download.ensure_bucket(client)
    session = nse_session()
    seen = check_new.load_seen()
    to_date = date.today()  # noqa: DTZ011 - a day of UTC-vs-IST drift is absorbed by the window
    from_date = to_date - timedelta(days=args.since_days)
    print(f"[backfill] window {from_date:%d-%m-%Y}..{to_date:%d-%m-%Y}, ledger has {len(seen)} seen")

    docs: list[dict] = []
    keys: set[str] = set()
    for i, symbol in enumerate(args.symbols):
        if i:
            time.sleep(DOWNLOAD_PACE_SECONDS)
        outcome = check_new.announcements_for(session, symbol, from_date, to_date)
        if outcome["rows"] is None:
            print(f"[backfill] {symbol}: announcements FAILED ({outcome['error']})")
            continue
        n = 0
        for row in outcome["rows"]:
            if check_new.classify(row) != "concall":
                continue
            doc = check_new.row_to_doc(row, "concall")
            if not doc:
                continue
            key = check_new.ledger_key(doc)
            if key in seen or key in keys:
                continue
            keys.add(key)
            docs.append(doc)
            n += 1
        print(f"[backfill] {symbol}: {n} new transcript(s)")

    if not docs:
        print("[backfill] nothing new to ingest")
        return 0

    print(f"[backfill] ingesting {len(docs)} transcript(s)…")
    result = ingest_docs(docs, session, client, label="backfill")

    newly_seen = {check_new.ledger_key(d) for d in result["skipped_docs"]}
    if result["embed"]["failed"] == 0:
        newly_seen |= {check_new.ledger_key(d) for d in result["ingested_docs"]}
    else:
        print(
            f"[backfill] {result['embed']['failed']} embed failure(s) — holding "
            f"{len(result['ingested_docs'])} newly-downloaded transcript(s) for a re-run"
        )
    if newly_seen:
        check_new.save_seen(seen | newly_seen)

    print(
        f"[backfill] done: {result['documents']} doc(s), {result['download_errors']} download error(s), "
        f"{result['chunks']} chunk(s), embed {result['embed']['embedded']} / "
        f"{result['embed']['skipped']} skipped / {result['embed']['failed']} failed"
    )
    return 1 if (result["download_errors"] or result["embed"]["failed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
