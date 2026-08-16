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

    all_chunks: list[dict] = []
    download_errors = 0
    total_pages = 0

    for i, doc in enumerate(docs):
        if i > 0:
            time.sleep(DOWNLOAD_PACE_SECONDS)

        result = download.ingest_one(session, client, doc)
        if result["outcome"] == "error":
            download_errors += 1
            continue

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
            f"[run] {symbol} {doc['doc_type']}: {len(pages)} page(s) extracted -> "
            f"running chunk total {len(all_chunks)}"
        )

    embed_counts = (
        embed_chunks(all_chunks, label=f"{symbol} ({len(all_chunks)} chunks)")
        if all_chunks
        else dict(EMPTY_EMBED_COUNTS)
    )

    return {
        "documents": len(docs) - download_errors,
        "download_errors": download_errors,
        "pages": total_pages,
        "chunks": len(all_chunks),
        "embed": embed_counts,
    }


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
    args = parser.parse_args(argv)
    results = run(symbols=args.symbols)
    any_failures = any(
        r["download_errors"] or r["embed"]["failed"] for r in results.values()
    )
    return 1 if any_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
