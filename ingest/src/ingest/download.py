"""Download the curated seed documents (ingest.seeds.SEED_DOCUMENTS), hash
them, upload the originals to Supabase Storage, and record them in
`documents`.

    uv run python -m ingest.download
    uv run python -m ingest.download --symbol TCS --symbol INFY

Idempotent — safe to re-run:
  - A seed with an nse_seq_id (concall entries) is checked against
    `documents.nse_seq_id` BEFORE downloading, so a re-run skips a known
    filing without spending a request on it at all.
  - Every seed is checked against `documents.sha256` AFTER downloading —
    this is what catches annual_report entries, which carry no seq_id, and
    guards against the same bytes turning up at a different URL.
  Either match skips the upload and the insert; neither writes anything.

Pacing, headers, and the session-priming dance all come from
ingest.nse_fetch — see that module for why: section-page cookie priming,
explicit gzip/deflate, an empty-body-200 retry, and DOWNLOAD_PACE_SECONDS
between requests. SOURCES.md §1 found no rate-limiting at that pace from
either a residential or GitHub Actions IP; downloading more files than that
probe did is not a reason to push harder against someone else's free
infrastructure.
"""

from __future__ import annotations

import argparse
import hashlib
import time

from ingest.config import get_settings
from ingest.db import get_client
from ingest.nse_fetch import (
    ANNOUNCEMENTS_REFERER,
    ANNUAL_REPORTS_REFERER,
    DOWNLOAD_PACE_SECONDS,
    FetchError,
    fetch_binary,
    nse_session,
)
from ingest.seeds import SEED_DOCUMENTS

STORAGE_BUCKET = "filings"


def _filename(url: str) -> str:
    return url.split("?", 1)[0].rsplit("/", 1)[-1]


def _object_path(symbol: str, doc_type: str, url: str) -> str:
    """Path within STORAGE_BUCKET — {symbol}/{doc_type}/{filename}."""
    return f"{symbol}/{doc_type}/{_filename(url)}"


def _referer_for(doc_type: str) -> str:
    return ANNUAL_REPORTS_REFERER if doc_type == "annual_report" else ANNOUNCEMENTS_REFERER


def ensure_bucket(client) -> None:
    """Create the filings bucket if it doesn't exist yet. Private — these
    PDFs feed the ingestion/retrieval pipeline, not a public asset."""
    try:
        client.storage.create_bucket(STORAGE_BUCKET, options={"public": False})
    except Exception as exc:
        message = str(exc).lower()
        if "already exists" not in message and "duplicate" not in message:
            raise


def _find_by(client, column: str, value) -> dict | None:
    result = (
        client.table("documents")
        .select("id, symbol, doc_type")
        .eq(column, value)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def ingest_one(session, client, doc: dict) -> str:
    """Download, dedup-check, upload, and insert one seed document.

    Returns "ingested", "skipped", or "error". Logs exactly one line either
    way — the summary this function's docstring promises the caller.
    """
    symbol, doc_type, period = doc["symbol"], doc["doc_type"], doc["period"]
    source_url, nse_seq_id = doc["source_url"], doc["nse_seq_id"]
    label = f"{symbol} {doc_type} seq={nse_seq_id if nse_seq_id is not None else '-'}"

    if nse_seq_id is not None:
        existing = _find_by(client, "nse_seq_id", nse_seq_id)
        if existing:
            print(f"[download] {label}: skip (nse_seq_id already in documents, id={existing['id']})")
            return "skipped"

    try:
        content, final_url = fetch_binary(session, source_url, referer=_referer_for(doc_type))
    except FetchError as exc:
        print(f"[download] {label}: ERROR — {exc}")
        return "error"

    sha256_hex = hashlib.sha256(content).hexdigest()

    existing = _find_by(client, "sha256", sha256_hex)
    if existing:
        print(
            f"[download] {label}: skip (sha256 already in documents, id={existing['id']}, "
            f"{len(content)} bytes)"
        )
        return "skipped"

    object_path = _object_path(symbol, doc_type, final_url)
    storage_path = f"{STORAGE_BUCKET}/{object_path}"
    client.storage.from_(STORAGE_BUCKET).upload(
        object_path, content, file_options={"content-type": "application/pdf"}
    )

    client.table("documents").insert(
        {
            "symbol": symbol,
            "doc_type": doc_type,
            "period": period,
            "source_url": source_url,
            "storage_path": storage_path,
            "sha256": sha256_hex,
            "nse_seq_id": nse_seq_id,
        }
    ).execute()

    redirected = "" if final_url == source_url else f", redirected -> {final_url}"
    print(
        f"[download] {label}: ingested — {len(content)} bytes, sha256={sha256_hex[:12]}…, "
        f"{storage_path}{redirected}"
    )
    return "ingested"


def run(symbols: list[str] | None = None) -> dict[str, int]:
    settings = get_settings()
    client = get_client(settings)
    ensure_bucket(client)
    session = nse_session()

    docs = SEED_DOCUMENTS
    if symbols:
        wanted = set(symbols)
        docs = [d for d in SEED_DOCUMENTS if d["symbol"] in wanted]

    counts = {"ingested": 0, "skipped": 0, "error": 0}
    for i, doc in enumerate(docs):
        if i > 0:
            time.sleep(DOWNLOAD_PACE_SECONDS)
        counts[ingest_one(session, client, doc)] += 1

    print(
        f"[download] done: {counts['ingested']} ingested, {counts['skipped']} skipped, "
        f"{counts['error']} error(s), out of {len(docs)}"
    )
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="limit to this symbol; repeatable. Default: every symbol in ingest.seeds.",
    )
    args = parser.parse_args(argv)
    counts = run(symbols=args.symbols)
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
