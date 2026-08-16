-- Initial schema: documents (filings/transcripts) + chunks (pgvector) + similarity search RPC.
-- Run via the Supabase SQL editor or `supabase db push`.

create extension if not exists vector;

create table if not exists documents (
  id uuid primary key default gen_random_uuid(),
  symbol text not null,                         -- NSE trading symbol, e.g. TCS
  doc_type text not null check (doc_type in ('filing', 'transcript')),
  title text not null,
  source_url text not null,
  published_at date,
  created_at timestamptz not null default now()
);

create table if not exists chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents (id) on delete cascade,
  content text not null,
  -- Must match the pinned embeddings provider's output size
  -- (Cloudflare Workers AI bge-base-en-v1.5 = 768 dims). Changing providers
  -- to a different dimensionality requires a new column/table + backfill.
  embedding vector(768) not null,
  chunk_index int not null,
  created_at timestamptz not null default now()
);

create index if not exists chunks_embedding_idx
  on chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create index if not exists chunks_document_id_idx on chunks (document_id);

-- Similarity search used by web/src/app/api/ask/route.ts.
create or replace function match_chunks(
  query_embedding vector(768),
  match_count int default 8
)
returns table (
  id uuid,
  content text,
  source text,
  similarity float
)
language sql stable
as $$
  select
    chunks.id,
    chunks.content,
    documents.title || ' (' || documents.source_url || ')' as source,
    1 - (chunks.embedding <=> query_embedding) as similarity
  from chunks
  join documents on documents.id = chunks.document_id
  order by chunks.embedding <=> query_embedding
  limit match_count;
$$;
