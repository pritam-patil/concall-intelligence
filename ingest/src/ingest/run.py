"""Orchestrates the full pipeline — download -> extract -> chunk -> embed —
for one or more symbols.

    uv run ingest run --symbol TCS
    uv run python -m ingest.run --symbol TCS --symbol INFY
    uv run python -m ingest.run                    # every symbol in ingest.seeds

Per symbol, every ingest.seeds.SEED_DOCUMENTS entry for it goes through:

  1. download.ingest_one() — from NSE, or resumed from Storage if this
     document is already in `documents` (see step 2's fallback below).
  2. extract.extract_pdf() — every page's text (PyMuPDF). extract_pdf()
     takes a path, not bytes, so content is written to a NamedTemporaryFile
     first rather than extract.py growing a bytes-accepting variant just
     for this one caller.
  3. chunk.chunk_page() — per page, into ~800-token overlapping chunks.

All of one symbol's chunks (across every one of its documents) accumulate
before step 4 — embedding — runs ONCE per symbol, not once per document.
That's deliberate: ingest.embed's batching and pacing are tuned around
"however many chunks a run has", and calling it once per document would
fragment a 12-document run into a dozen small batched calls instead of a
few full-sized ones, for no benefit.

A document that errors at the download step (network failure, 404, ...)
is skipped for extract/chunk/embed too — there's nothing to extract from
a document that was never fetched. Logged and counted, not fatal to the
rest of the symbol's documents or to other symbols in the same run.

A document download.ingest_one() reports as "skipped" (already in
`documents` from an earlier run) has no bytes attached — re-downloading
from NSE for a file already sitting in Storage would be pointless, so
those bytes come from Storage instead (download.fetch_from_storage()).
This is what makes a re-run of `ingest run --symbol X` actually resume
cleanly end to end, not just at the embed step: nothing gets re-fetched
from NSE, and only chunks that don't yet have an embedding from the
configured provider get re-embedded (ingest.embed's own resumability).
"""

from __future__ import annotations

import argparse
import tempfile
import time

from ingest import chunk as chunk_mod
from ingest import download, extract
from ingest.config import get_settings
from ingest.db import get_client
from ingest.embed import embed_chunks
from ingest.nse_fetch import DOWNLOAD_PACE_SECONDS, nse_session
from ingest.seeds import SEED_DOCUMENTS

EMPTY_EMBED_COUNTS = {"total": 0, "embedded": 0, "skipped": 0, "failed": 0}


def _content_for(client, result: dict) -> bytes:
    """The document's PDF bytes, how ever download.ingest_one() got it (a
    fresh download) or didn't (fetched back out of Storage instead)."""
    if result["content"] is not None:
        return result["content"]
    return download.fetch_from_storage(client, result["storage_path"])


def ingest_docs(docs: list[dict], session, client, *, label: str) -> dict:
    """Run download -> extract -> chunk -> embed for a list of arbitrary
    document dicts (each shaped like an ingest.seeds.SEED_DOCUMENTS entry:
    {symbol, doc_type, period, source_url, nse_seq_id}). Nothing here is
    seed-specific — this is the reusable pipeline unit shared by the seed
    backfill (run_symbol) and the nightly delta path (ingest.check_new).

    Embedding runs ONCE over all chunks (not once per document) for the reason
    in this module's docstring. Returns the usual counts plus `processed_docs`:
    the docs that reached the `documents` table (downloaded or dedup-skipped) —
    the delta path records only those in its seen-ledger, so a doc that ERRORED
    at download is retried next run rather than marked seen.
    """
    all_chunks: list[dict] = []
    download_errors = 0
    total_pages = 0
    ingested: list[dict] = []  # newly downloaded this run
    skipped: list[dict] = []  # already in `documents` (dedup skip)

    for i, doc in enumerate(docs):
        if i > 0:
            time.sleep(DOWNLOAD_PACE_SECONDS)

        result = download.ingest_one(session, client, doc)
        outcome = result["outcome"]
        if outcome == "error":
            download_errors += 1
            continue
        (skipped if outcome == "skipped" else ingested).append(doc)

        content = _content_for(client, result)
        document_id = result["document_id"]

        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(content)
            tmp.flush()
            pages = extract.extract_pdf(tmp.name, document_id)

        total_pages += len(pages)
        for page_row in pages:
            all_chunks.extend(
                chunk_mod.chunk_page(document_id, page_row["page"], page_row["text"])
            )
        print(
            f"[run] {doc['symbol']} {doc['doc_type']}: {len(pages)} page(s) extracted -> "
            f"running chunk total {len(all_chunks)}"
        )

    embed_counts = (
        embed_chunks(all_chunks, label=f"{label} ({len(all_chunks)} chunks)")
        if all_chunks
        else dict(EMPTY_EMBED_COUNTS)
    )

    return {
        "documents": len(docs) - download_errors,
        "download_errors": download_errors,
        "pages": total_pages,
        "chunks": len(all_chunks),
        "embed": embed_counts,
        # Split so the delta path can advance its ledger safely: skipped docs are
        # already fully in the DB; newly-ingested docs are only "done" if their
        # embeddings landed (embed can 429 on the shared free-tier quota).
        "ingested_docs": ingested,
        "skipped_docs": skipped,
    }


def run_symbol(symbol: str, session, client) -> dict:
    docs = [d for d in SEED_DOCUMENTS if d["symbol"] == symbol]
    if not docs:
        print(f"[run] {symbol}: no seed documents for this symbol in ingest.seeds")
        return {
            "documents": 0,
            "download_errors": 0,
            "pages": 0,
            "chunks": 0,
            "embed": dict(EMPTY_EMBED_COUNTS),
        }
    return ingest_docs(docs, session, client, label=symbol)


def run(symbols: list[str] | None = None) -> dict[str, dict]:
    settings = get_settings()
    client = get_client(settings)
    download.ensure_bucket(client)
    session = nse_session()

    wanted = symbols or sorted({d["symbol"] for d in SEED_DOCUMENTS})
    results = {}
    for symbol in wanted:
        print(f"[run] === {symbol} ===")
        results[symbol] = run_symbol(symbol, session, client)

    print("[run] === summary ===")
    totals = {"documents": 0, "download_errors": 0, "pages": 0, "chunks": 0, "embedded": 0}
    for symbol, r in results.items():
        totals["documents"] += r["documents"]
        totals["download_errors"] += r["download_errors"]
        totals["pages"] += r["pages"]
        totals["chunks"] += r["chunks"]
        totals["embedded"] += r["embed"]["embedded"]
        print(
            f"[run] {symbol}: {r['documents']} document(s) "
            f"({r['download_errors']} download error(s)), {r['pages']} page(s), "
            f"{r['chunks']} chunk(s), embed: {r['embed']['embedded']} embedded / "
            f"{r['embed']['skipped']} skipped / {r['embed']['failed']} failed"
        )
    print(
        f"[run] total: {totals['documents']} document(s), {totals['pages']} page(s), "
        f"{totals['chunks']} chunk(s), {totals['embedded']} embedded"
    )
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="limit to this symbol; repeatable. Default: every symbol in ingest.seeds.",
    )
    parser.add_argument(
        "--check-for-new",
        action="store_true",
        help="nightly mode: discover & ingest NEW filings for the tracked companies "
        "(query announcements by date range, filter transcripts/annual reports, "
        "dedupe against the committed seq_id ledger) instead of the seed backfill.",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="--check-for-new lookback window in days (default: check_new.DEFAULT_SINCE_DAYS).",
    )
    parser.add_argument(
        "--assert-access",
        action="store_true",
        help="probe NSE announcements + nsearchives from this host; exit 0 if BOTH "
        "reachable, nonzero otherwise. Step 1 of the nightly job (per-endpoint gate).",
    )
    args = parser.parse_args(argv)

    # Delta/discovery modes live in ingest.check_new (imported lazily so the
    # seed backfill path never pulls it in, and to avoid an import cycle —
    # check_new imports ingest_docs from this module).
    if args.assert_access:
        from ingest.check_new import assert_access_cli

        return assert_access_cli()
    if args.check_for_new:
        from ingest.check_new import run_check_for_new

        return run_check_for_new(since_days=args.since_days)

    results = run(symbols=args.symbols)
    any_failures = any(
        r["download_errors"] or r["embed"]["failed"] for r in results.values()
    )
    return 1 if any_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
