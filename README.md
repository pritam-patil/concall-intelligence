# concall-intelligence

Source-cited Q&A over NSE filings and earnings-call transcripts, built to
run entirely on free tiers. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
full design (ADR) and a data-flow diagram.

## Layout

```
web/               Next.js App Router app (TypeScript) — the Q&A UI + /api/ask
ingest/            Python 3.11 ingestion pipeline — fetch, parse, chunk, embed, store
supabase/          SQL migrations (pgvector schema, match_chunks RPC)
```

## Quickstart

```bash
# Web app
cd web && cp .env.example .env.local && npm install && npm run dev

# Ingestion pipeline
cd ingest && cp .env.example .env && uv sync   # or: pip install -r requirements.txt
```

Apply [`supabase/migrations/0001_init.sql`](supabase/migrations/0001_init.sql) to your Supabase
project before running either.
