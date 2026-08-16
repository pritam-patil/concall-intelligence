# ingest

Python ingestion pipeline: fetch NSE filings and earnings-call transcripts,
parse, chunk, embed, and store in Supabase (pgvector). See
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the overall design.

## Setup

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync
cp .env.example .env   # then fill in values
```

Or with plain pip:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in values
```

## Usage

```bash
uv run ingest --help
uv run ingest filings --symbol TCS
uv run ingest transcripts --symbol TCS
```

## Layout

```
src/ingest/
  config.py            # env-backed Settings, loaded once via get_settings()
  cli.py                # `ingest` command group (click)
  db.py                 # Supabase client
  providers/
    embeddings.py        # EmbeddingsProvider interface + CloudflareBgeEmbeddings (pinned)
    generation.py         # GenerationProvider interface + GeminiFlashGeneration (pinned)
```
