"""CLI entry point for the ingestion pipeline.

    uv run ingest --help
    # or: python -m ingest --help

This is a skeleton: subcommands are stubs to be filled in as the fetch/parse/
chunk/embed/store stages are implemented.
"""

from __future__ import annotations

import click


@click.group()
def main() -> None:
    """Fetch, parse, chunk, embed, and store NSE filings and earnings-call transcripts."""


@main.command()
@click.option("--symbol", required=True, help="NSE trading symbol, e.g. TCS.")
def filings(symbol: str) -> None:
    """Fetch and ingest corporate filings for SYMBOL."""
    click.echo(f"[stub] would fetch filings for {symbol}")


@main.command()
@click.option("--symbol", required=True, help="NSE trading symbol, e.g. TCS.")
def transcripts(symbol: str) -> None:
    """Fetch and ingest earnings-call transcripts for SYMBOL."""
    click.echo(f"[stub] would fetch transcripts for {symbol}")


@main.command()
@click.option(
    "--symbol",
    "symbols",
    multiple=True,
    help="Limit to this symbol; repeatable. Default: every symbol in ingest.seeds.",
)
def download(symbols: tuple[str, ...]) -> None:
    """Download the curated seed documents (ingest.seeds), hash, upload to
    Storage, and record them in `documents`. Not the same as filings/
    transcripts above — those are for live discovery (not yet built); this
    ingests the fixed, hand-picked set documented in SOURCES.md."""
    from ingest.download import run

    counts = run(symbols=list(symbols) or None)
    if counts["error"]:
        raise SystemExit(1)


@main.command("backfill-dating")
@click.option("--dry-run", is_flag=True, help="Print the plan without writing.")
def backfill_dating(dry_run: bool) -> None:
    """Fill `filed_at` (and concall `period`) on documents ingested before
    those were derived. Idempotent, offline — it reads only source_url. Run
    once after applying the documents_filed_at migration."""
    from ingest.backfill import run as run_backfill

    run_backfill(dry_run=dry_run)


@main.command()
@click.option(
    "--symbol",
    "symbols",
    multiple=True,
    help="Limit to this symbol; repeatable. Default: every symbol in ingest.seeds.",
)
def run(symbols: tuple[str, ...]) -> None:
    """Full pipeline for SYMBOL(s): download -> extract -> chunk -> embed.
    See ingest/src/ingest/run.py's module docstring for how the stages
    connect and how a re-run resumes."""
    from ingest.run import run as run_pipeline

    results = run_pipeline(symbols=list(symbols) or None)
    if any(r["download_errors"] or r["embed"]["failed"] for r in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
