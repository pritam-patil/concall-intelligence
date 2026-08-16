"""Enables `python -m ingest`, per cli.py's own docstring."""

from ingest.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
