"""check-for-new mode: discover and ingest newly published NSE filings for the
tracked companies, unattended. This is what the nightly GitHub Actions cron
runs (.github/workflows/nightly-ingest.yml).

Flow, per run:
  1. ASSERT ACCESS (step 1, per-endpoint). Probe BOTH hosts a full ingest needs
     — the announcements API (www.nseindia.com) and the PDF archive
     (nsearchives.nseindia.com) — because cloud egress can reach one and not
     the other (nse-assist's Workers spike; see nse_fetch.py). Reachability
     from GitHub runners was settled by the committed probe in
     results/*_gha.json (both 200 on 2026-08-16) and nse-assist's notify.yml
     history; this gate guards against a REGRESSION, and on failure the
     workflow opens a GitHub issue and exits nonzero rather than failing quiet.
  2. DISCOVER. For every tracked symbol (the Supabase `companies` table), query
     /api/corporate-announcements over a date window and keep rows whose
     desc/attchmntText contains "transcript" or "annual report" — the keyword
     recipe from scripts/probe_nse_access.py / SOURCES.md §2.
  3. DEDUPE against the committed seq_id seen-ledger (ingest/state/
     seen_seq_ids.json) — the same commit-state pattern as nse-assist's alert
     scheduler (data/notify_state.json): a filing is "seen" iff its key
     (`seq:<seq_id>`) is in the ledger. Only NOT-seen filings are ingested.
  4. INGEST the deltas through the shared run.ingest_docs pipeline (download ->
     extract -> chunk -> embed). download.ingest_one's own seq_id/sha256 dedup
     is the backstop, so re-checking an already-ingested filing is a no-op.
  5. RECORD the ingested filings' keys back into the ledger. The WORKFLOW then
     git-commits the ledger file so the next run starts from it.

The ledger PATH is overridable via SEEN_LEDGER_PATH. That is deliberate: the
bare-runner lesson from nse-assist is that a committed artifact must resolve to
the SAME path a fresh `actions/checkout` sees, and an editable-vs-installed
package can move `__file__`. CI sets SEEN_LEDGER_PATH to an absolute repo path
so the ledger the code writes is exactly the file the commit step commits; and
tests isolate it to a temp dir the same way.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from ingest import download
from ingest.config import get_settings
from ingest.db import get_client
from ingest.nse_fetch import (
    ANNOUNCEMENTS_REFERER,
    ANNOUNCEMENTS_URL,
    DOWNLOAD_PACE_SECONDS,
    FetchError,
    fetch_binary,
    nse_session,
    probe,
)

# Nightly lookback. The ledger dedup makes a generous window harmless
# (re-discovered filings are skipped), so this is sized to tolerate a few
# missed runs / late filings, not tuned to the day.
DEFAULT_SINCE_DAYS = 7

# The keyword recipe (SOURCES.md §2 / probe_nse_access.SEED_KEYWORDS): a filing
# is a transcript or annual report if desc/attchmntText contains one of these.
CONCALL_KEYWORD = "transcript"
ANNUAL_REPORT_KEYWORD = "annual report"

# assert-access probes one symbol's recent announcements (to reach the API) and
# downloads one of its attachments (to reach nsearchives). RELIANCE files often,
# so a short window reliably yields an attachment to test.
ASSERT_SYMBOL = "RELIANCE"
ASSERT_LOOKBACK_DAYS = 90


# --- seen-ledger (committed commit-state) -------------------------------------

# Default lives in the repo at ingest/state/seen_seq_ids.json. __file__ is
# ingest/src/ingest/check_new.py, so parents[2] is ingest/.
_DEFAULT_SEEN_PATH = Path(__file__).resolve().parents[2] / "state" / "seen_seq_ids.json"


def seen_path() -> Path:
    """The ledger path — SEEN_LEDGER_PATH env override wins (CI/tests isolate it
    to an unambiguous absolute path), else the committed repo default."""
    override = os.environ.get("SEEN_LEDGER_PATH")
    return Path(override) if override else _DEFAULT_SEEN_PATH


def load_seen() -> set[str]:
    """The set of already-seen filing keys. Absent/corrupt ledger -> empty set
    (a valid cold start: everything in the window is treated as new)."""
    path = seen_path()
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return set()
    seen = data.get("seen", []) if isinstance(data, dict) else []
    return {str(k) for k in seen}


def save_seen(keys: set[str]) -> None:
    """Persist the ledger as a SORTED list (stable diffs), matching nse-assist's
    notify_state.json shape."""
    path = seen_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": sorted(keys)}, indent=1) + "\n")


def ledger_key(doc: dict) -> str:
    """A filing's stable ledger key: `seq:<seq_id>` when the announcements feed
    supplied NSE's own filing id (every announcement row does), else a
    symbol+URL fallback (defensive — should not occur for announcements)."""
    if doc.get("nse_seq_id") is not None:
        return f"seq:{doc['nse_seq_id']}"
    return f"{doc['symbol']}|{doc['source_url']}"


# --- discovery ----------------------------------------------------------------


def classify(row: dict) -> str | None:
    """doc_type for an announcement row via the keyword recipe, or None to
    ignore. 'transcript' -> concall wins over 'annual report' -> annual_report
    (a transcript row occasionally mentions the annual report in passing)."""
    text = f"{row.get('desc', '')} {row.get('attchmntText', '')}".lower()
    if CONCALL_KEYWORD in text:
        return "concall"
    if ANNUAL_REPORT_KEYWORD in text:
        return "annual_report"
    return None


def row_to_doc(row: dict, doc_type: str) -> dict | None:
    """A SEED_DOCUMENTS-shaped dict from an announcement row, or None if it has
    no attachment. period is left None — NSE's announcement prose doesn't carry
    a reliably parseable period (same choice as seeds.py for concalls)."""
    url = row.get("attchmntFile")
    if not url:
        return None
    seq = row.get("seq_id")
    nse_seq_id = int(seq) if seq is not None and str(seq).strip().isdigit() else None
    return {
        "symbol": row.get("symbol"),
        "doc_type": doc_type,
        "period": None,
        "source_url": url,
        "nse_seq_id": nse_seq_id,
    }


def announcements_for(session, symbol: str, from_date: date, to_date: date) -> dict:
    """probe() outcome for one symbol's announcements over [from_date, to_date]
    (NSE date format dd-mm-yyyy)."""
    params = {
        "index": "equities",
        "symbol": symbol,
        "from_date": from_date.strftime("%d-%m-%Y"),
        "to_date": to_date.strftime("%d-%m-%Y"),
    }
    return probe(session, f"announcements-{symbol}", ANNOUNCEMENTS_URL, params, ANNOUNCEMENTS_REFERER)


def discover_new(session, symbols, from_date, to_date, seen: set[str]) -> list[dict]:
    """Not-yet-seen transcript/annual-report filings across the tracked symbols.
    A per-symbol announcements failure is logged and skipped, never fatal — one
    symbol's outage shouldn't sink the whole nightly run."""
    docs: list[dict] = []
    keyset = set()
    for i, symbol in enumerate(symbols):
        if i > 0:
            time.sleep(DOWNLOAD_PACE_SECONDS)
        outcome = announcements_for(session, symbol, from_date, to_date)
        if outcome["rows"] is None:
            print(f"[check] {symbol}: announcements FAILED ({outcome['error']}) — skipping")
            continue
        new_here = 0
        for row in outcome["rows"]:
            doc_type = classify(row)
            if doc_type is None:
                continue
            doc = row_to_doc(row, doc_type)
            if doc is None:
                continue
            key = ledger_key(doc)
            if key in seen or key in keyset:
                continue
            keyset.add(key)
            docs.append(doc)
            new_here += 1
        print(
            f"[check] {symbol}: {len(outcome['rows'])} announcement(s), "
            f"{new_here} new transcript/annual-report filing(s)"
        )
    return docs


# --- access assertion (step 1, per-endpoint) ----------------------------------


def assert_access(session) -> bool:
    """True iff BOTH the announcements API AND nsearchives answer this host.
    Gated per-endpoint (the nse-assist lesson): the announcements host answering
    says nothing about whether the archive host serves a PDF."""
    today = date.today()  # noqa: DTZ011 - a day of drift is irrelevant in a 90-day window
    outcome = announcements_for(session, ASSERT_SYMBOL, today - timedelta(days=ASSERT_LOOKBACK_DAYS), today)
    if outcome["rows"] is None:
        print(f"[assert] announcements UNREACHABLE ({outcome['error']})")
        return False
    print(f"[assert] announcements reachable ({len(outcome['rows'])} row(s))")

    url = next((r.get("attchmntFile") for r in outcome["rows"] if r.get("attchmntFile")), None)
    if not url:
        print("[assert] announcements reachable but no attachment URL to test nsearchives")
        return False
    try:
        content, _ = fetch_binary(session, url, referer=ANNOUNCEMENTS_REFERER)
    except FetchError as exc:
        print(f"[assert] nsearchives UNREACHABLE ({exc})")
        return False
    print(f"[assert] nsearchives reachable ({len(content)} bytes from {url})")
    return True


def assert_access_cli() -> int:
    """`--assert-access` entrypoint: 0 if both hosts reachable, 1 otherwise."""
    return 0 if assert_access(nse_session()) else 1


# --- orchestration ------------------------------------------------------------


def fetch_tracked_symbols(client) -> list[str]:
    """The tracked-company universe — the Supabase `companies` table, the same
    source the web selector reads, and the FK target documents.symbol must
    satisfy. Sorted for a stable run order."""
    resp = client.table("companies").select("symbol").execute()
    return sorted(row["symbol"] for row in (resp.data or []))


def run_check_for_new(since_days: int | None = None) -> int:
    """Discover, ingest, and record new filings. Returns a process exit code:
    0 on a clean run (including 'nothing new'), 2 if the access gate failed,
    1 if any discovered filing errored at download (so the run is visibly red
    and the ledger did NOT record the failed one — it retries next run)."""
    since = since_days if since_days is not None else DEFAULT_SINCE_DAYS
    settings = get_settings()
    client = get_client(settings)
    download.ensure_bucket(client)
    session = nse_session()

    # Step 1: per-endpoint access gate. In CI this also runs as its own step so
    # a failure opens an issue; running it here too keeps a direct `--check-for-
    # new` invocation from silently proceeding against a dead host.
    if not assert_access(session):
        print("[check] ABORT — NSE access assertion failed (see above)")
        return 2

    symbols = fetch_tracked_symbols(client)
    if not symbols:
        print("[check] no tracked symbols in the companies table — nothing to do")
        return 0
    print(f"[check] tracked symbols: {', '.join(symbols)}")

    seen = load_seen()
    print(f"[check] ledger: {len(seen)} filing(s) already seen ({seen_path()})")

    to_date = date.today()  # noqa: DTZ011 - IST-vs-UTC day boundary is absorbed by the lookback window
    from_date = to_date - timedelta(days=since)
    print(f"[check] window: {from_date:%d-%m-%Y} .. {to_date:%d-%m-%Y} ({since}d)")

    docs = discover_new(session, symbols, from_date, to_date, seen)
    if not docs:
        print("[check] no new filings — ledger unchanged")
        return 0

    print(f"[check] ingesting {len(docs)} new filing(s)…")
    result = ingest_new_docs(docs, session, client)

    # Advance the ledger safely. A dedup-SKIPPED filing is already fully in the
    # DB → always record it. A newly-INGESTED filing is only "done" if its
    # embeddings landed; if any embed batch failed (typically the shared
    # Cloudflare free-tier quota), HOLD the new filings out of the ledger so the
    # next run retries them — download.ingest_one skips the re-download and
    # embed_chunks resumes the un-embedded chunks (uuid dedup), completing them.
    embed_failed = result["embed"]["failed"]
    newly_seen = {ledger_key(doc) for doc in result["skipped_docs"]}
    if embed_failed == 0:
        newly_seen |= {ledger_key(doc) for doc in result["ingested_docs"]}
    else:
        held = len(result["ingested_docs"])
        print(
            f"[check] {embed_failed} embed failure(s) — holding {held} newly-downloaded "
            "filing(s) OUT of the ledger so the next run completes them"
        )
    if newly_seen:
        save_seen(seen | newly_seen)
        print(f"[check] ledger: recorded {len(newly_seen)} filing(s), now {len(seen | newly_seen)} total")

    print(
        f"[check] done: {result['documents']} document(s) "
        f"({result['download_errors']} download error(s)), {result['chunks']} chunk(s), "
        f"embed {result['embed']['embedded']} / skipped {result['embed']['skipped']} / "
        f"failed {embed_failed}"
    )
    return 1 if (result["download_errors"] or embed_failed) else 0


def ingest_new_docs(docs, session, client) -> dict:
    """Thin wrapper over run.ingest_docs (imported lazily to avoid an import
    cycle: run.py imports this module inside main())."""
    from ingest.run import ingest_docs

    return ingest_docs(docs, session, client, label="check-for-new")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--assert-access",
        action="store_true",
        help="probe NSE announcements + nsearchives; exit 0 if both reachable, else 1.",
    )
    parser.add_argument(
        "--since-days", type=int, default=None, help=f"lookback window (default {DEFAULT_SINCE_DAYS})."
    )
    args = parser.parse_args(argv)
    if args.assert_access:
        return assert_access_cli()
    return run_check_for_new(since_days=args.since_days)


if __name__ == "__main__":
    raise SystemExit(main())
