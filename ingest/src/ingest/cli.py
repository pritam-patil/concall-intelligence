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


if __name__ == "__main__":
    main()
