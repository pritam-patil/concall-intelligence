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
from ingest.period import derive_concall_period, filed_at_from_url
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


def find_by(client, column: str, value) -> dict | None:
    result = (
        client.table("documents")
        .select("id, symbol, doc_type, storage_path")
        .eq(column, value)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def fetch_from_storage(client, storage_path: str) -> bytes:
    """The bytes back out of Storage for a document ingest_one() skipped
    (already in `documents`, so not re-downloaded from NSE) — this is what
    lets a resumed run.py still extract/chunk/embed a document without
    hitting NSE again for a file that's already sitting in the bucket.
    `storage_path` is the full `{bucket}/{object_path}` value stored on the
    row (see `documents.storage_path`'s column comment); split off the
    bucket since the Storage API wants them separate."""
    bucket, _, object_path = storage_path.partition("/")
    return client.storage.from_(bucket).download(object_path)


def dating_for(doc: dict) -> tuple[str | None, str | None, str]:
    """`(period, filed_at_iso, how)` for a document about to be inserted.

    ONE place derives dating for every source — the curated seeds, the
    nightly check-for-new discovery, and the backfill — so a concall row can
    never end up dated by one rule here and another rule there.

    An explicit `period` on the doc (annual reports carry a real structured
    one from the /api/annual-reports feed) is authoritative and is never
    second-guessed. Concalls, which have none, get ingest.period's derivation
    from the filing text and the NSE filename stamp; see that module for why
    they are datable at all after previously being left null.
    """
    source_url = doc["source_url"]
    if doc.get("period"):
        filed_at = filed_at_from_url(source_url)
        return doc["period"], filed_at.isoformat() if filed_at else None, "given"
    period, filed_at, how = derive_concall_period(source_url, doc.get("filing_text", ""))
    return period, filed_at.isoformat() if filed_at else None, how


def ingest_one(session, client, doc: dict) -> dict:
    """Download, dedup-check, upload, and insert one seed document.

    Returns {"outcome": "ingested"|"skipped"|"error", "document_id",
    "content", "storage_path"}. `document_id`/`storage_path` are None only
    for "error". `content` (the raw PDF bytes) is set for "ingested" (just
    downloaded) and None for "skipped" — a caller that needs the bytes for
    a skipped document should fetch_from_storage(client, storage_path)
    instead of re-downloading from NSE for no reason (storage_path is
    right there in this same return value — no extra query needed). Logs
    exactly one line either way — the summary this function's docstring
    promises the caller.
    """
    symbol, doc_type = doc["symbol"], doc["doc_type"]
    source_url, nse_seq_id = doc["source_url"], doc["nse_seq_id"]
    period, filed_at, dated_how = dating_for(doc)
    label = f"{symbol} {doc_type} seq={nse_seq_id if nse_seq_id is not None else '-'}"

    if nse_seq_id is not None:
        existing = find_by(client, "nse_seq_id", nse_seq_id)
        if existing:
            print(f"[download] {label}: skip (nse_seq_id already in documents, id={existing['id']})")
            return {
                "outcome": "skipped",
                "document_id": existing["id"],
                "content": None,
                "storage_path": existing["storage_path"],
            }

    try:
        content, final_url = fetch_binary(session, source_url, referer=_referer_for(doc_type))
    except FetchError as exc:
        print(f"[download] {label}: ERROR — {exc}")
        return {"outcome": "error", "document_id": None, "content": None, "storage_path": None}

    sha256_hex = hashlib.sha256(content).hexdigest()

    existing = find_by(client, "sha256", sha256_hex)
    if existing:
        print(
            f"[download] {label}: skip (sha256 already in documents, id={existing['id']}, "
            f"{len(content)} bytes)"
        )
        return {
            "outcome": "skipped",
            "document_id": existing["id"],
            "content": None,
            "storage_path": existing["storage_path"],
        }

    object_path = _object_path(symbol, doc_type, final_url)
    storage_path = f"{STORAGE_BUCKET}/{object_path}"
    # x-upsert: the dedup checks above only rule out a `documents` ROW for
    # this content — not an orphaned Storage OBJECT at the same path (a
    # prior run that uploaded but crashed before the insert, for example).
    # Safe to overwrite rather than error: object_path is derived from
    # (symbol, doc_type, filename), so a path collision here already means
    # "the same file", and we're about to write those exact bytes anyway.
    client.storage.from_(STORAGE_BUCKET).upload(
        object_path,
        content,
        file_options={"content-type": "application/pdf", "x-upsert": "true"},
    )

    result = (
        client.table("documents")
        .insert(
            {
                "symbol": symbol,
                "doc_type": doc_type,
                "period": period,
                "filed_at": filed_at,
                "source_url": source_url,
                "storage_path": storage_path,
                "sha256": sha256_hex,
                "nse_seq_id": nse_seq_id,
            }
        )
        .execute()
    )
    document_id = result.data[0]["id"]

    redirected = "" if final_url == source_url else f", redirected -> {final_url}"
    # `period` is printed with the signal it came from — a period derived
    # from the filing date is a rule, not a reading, and the log is where
    # that stays auditable (ingest.period's module docstring).
    print(
        f"[download] {label}: ingested — {len(content)} bytes, sha256={sha256_hex[:12]}…, "
        f"period={period or '-'} (via {dated_how}), filed_at={filed_at or '-'}, "
        f"{storage_path}{redirected}"
    )
    return {
        "outcome": "ingested",
        "document_id": document_id,
        "content": content,
        "storage_path": storage_path,
    }


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
        counts[ingest_one(session, client, doc)["outcome"]] += 1

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
