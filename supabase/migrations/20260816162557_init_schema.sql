-- Baseline schema for concall-intelligence. See ARCHITECTURE.md for the
-- overall design and SOURCES.md for where documents/chunks data comes from.
--
-- This supersedes the provisional schema sketched in the first pass of this
-- repo (a simpler documents/chunks pair with no companies table). Nothing
-- was ever deployed against that version, so this is a clean baseline, not
-- an incremental change — do not expect a prior migration to have run.

create extension if not exists vector;
-- gen_random_uuid() is core Postgres since v13, but Supabase projects also
-- ship pgcrypto; declared explicitly so this migration doesn't depend on
-- which one happens to already be enabled.
create extension if not exists pgcrypto;

create type doc_type as enum ('annual_report', 'concall', 'announcement');


-- --- companies ---------------------------------------------------------------

create table companies (
  symbol   text primary key,
  name     text not null,
  isin     text,
  industry text
);

comment on table companies is
  'NSE-listed companies in the ingestion universe. symbol is NSE''s own '
  'ticker (e.g. TCS) and the natural key documents.symbol hangs off of.';
comment on column companies.isin is
  'From NSE''s equity master list (data/EQUITY_L.csv), not the '
  'announcements feed''s sm_isin field — the two disagreed for HDFCBANK '
  'during seeding (INE040A01034 vs INE040A01018); the master list won. '
  'See seed.sql.';
comment on column companies.industry is
  'NSE''s own smIndustry classification from a live announcement row, '
  'where NSE actually populates it. Null where it does not (e.g. TMCV/TMPV '
  '— checked against their entire announcement history, never populated) '
  '— left null rather than guessed. See seed.sql.';


-- --- documents -----------------------------------------------------------------

create table documents (
  id           uuid primary key default gen_random_uuid(),
  symbol       text not null references companies (symbol) on delete cascade,
  doc_type     doc_type not null,
  period       text,
  source_url   text not null,
  storage_path text,
  sha256       text,
  nse_seq_id   bigint unique,
  ingested_at  timestamptz not null default now()
);

comment on table documents is
  'One row per source filing (annual report, concall transcript, or '
  'announcement) discovered on NSE. Chunking/embedding happens in chunks.';
comment on column documents.period is
  'Free text: fiscal year for annual reports ("FY2025-26"), quarter for '
  'concalls ("Q1 FY27"). Null for announcements and anything else without '
  'a natural period.';
comment on column documents.storage_path is
  'Path in the Supabase Storage bucket once the raw file has been '
  'downloaded. Null between discovery (row created from an '
  'announcements/annual-reports probe) and download — ingestion is '
  'two-phase, not one insert per document.';
comment on column documents.sha256 is
  'Hash of the downloaded file bytes. Null until downloaded; used for '
  'integrity checking and re-download dedup.';
comment on column documents.nse_seq_id is
  'NSE''s own unique id for an announcement filing (the seq_id field from '
  '/api/corporate-announcements — see SOURCES.md). Null for annual-report '
  'rows, which carry no seq_id at all. Unique so a second ingestion run is '
  'idempotent by ON CONFLICT on this column, for the doc_types that have it.';

create index documents_symbol_idx on documents (symbol);
create index documents_doc_type_idx on documents (doc_type);


-- --- chunks ----------------------------------------------------------------------

create table chunks (
  id            uuid primary key default gen_random_uuid(),
  document_id   uuid not null references documents (id) on delete cascade,
  page          integer,
  section       text,
  content       text not null,
  token_count   integer not null,
  embedding     vector(768),
  -- Generated, not a plain column: keeps the tsvector in sync with content
  -- automatically and is what the GIN index below actually indexes.
  content_tsv   tsvector generated always as (to_tsvector('english', content)) stored
);

comment on table chunks is
  'Chunked, embedded text from one document, for retrieval. embedding is '
  '768-dim to match the pinned embeddings provider (Cloudflare Workers AI '
  'bge-base-en-v1.5 — ARCHITECTURE.md §3.2); both ingest/ and web/ must '
  'stay on the same model or this index is meaningless.';
comment on column chunks.page is
  'Source PDF page number, when known. Null for chunks that do not map to '
  'a single page (e.g. text merged across a page break).';
comment on column chunks.embedding is
  'Nullable on purpose: a chunk can be written with its text before its '
  'embedding is computed, so a batched embed step can run decoupled from '
  'text extraction rather than one API call per chunk inline.';

create index chunks_document_id_idx on chunks (document_id);

-- ANN index on embedding — HNSW over IVFFlat: IVFFlat's `lists` parameter
-- has to be tuned to a row count that does not exist yet on a table
-- starting empty (get it wrong and the index is either useless or a
-- full scan in disguise until a manual REINDEX), where HNSW builds
-- incrementally and needs no such upfront tuning. vector_cosine_ops
-- because match_chunks() below orders by <=> (cosine distance) — bge
-- embeddings are meant to be compared by cosine similarity.
create index chunks_embedding_hnsw_idx
  on chunks using hnsw (embedding vector_cosine_ops);

-- Full-text index for a keyword fallback (or hybrid) alongside vector
-- search — see search_chunks_text() below, the query-side counterpart.
create index chunks_content_tsv_idx
  on chunks using gin (content_tsv);


-- --- retrieval RPCs, called from web/src/app/api/ask/route.ts -----------------

create or replace function match_chunks(
  query_embedding vector(768),
  match_count int default 8
)
returns table (
  id uuid,
  content text,
  page integer,
  section text,
  source text,
  symbol text,
  similarity float
)
language sql stable
as $$
  select
    chunks.id,
    chunks.content,
    chunks.page,
    chunks.section,
    documents.symbol || ' ' || documents.doc_type::text
      || coalesce(' (' || documents.period || ')', '')
      || ' — ' || documents.source_url as source,
    documents.symbol,
    1 - (chunks.embedding <=> query_embedding) as similarity
  from chunks
  join documents on documents.id = chunks.document_id
  where chunks.embedding is not null
  order by chunks.embedding <=> query_embedding
  limit match_count;
$$;

comment on function match_chunks(vector, int) is
  'Vector similarity search over chunks with an embedding. Cosine distance '
  '(<=>), matching the HNSW index above and bge''s intended metric.';

create or replace function search_chunks_text(
  query text,
  match_count int default 8
)
returns table (
  id uuid,
  content text,
  page integer,
  section text,
  source text,
  symbol text,
  rank float
)
language sql stable
as $$
  select
    chunks.id,
    chunks.content,
    chunks.page,
    chunks.section,
    documents.symbol || ' ' || documents.doc_type::text
      || coalesce(' (' || documents.period || ')', '')
      || ' — ' || documents.source_url as source,
    documents.symbol,
    ts_rank(chunks.content_tsv, websearch_to_tsquery('english', query)) as rank
  from chunks
  join documents on documents.id = chunks.document_id
  where chunks.content_tsv @@ websearch_to_tsquery('english', query)
  order by rank desc
  limit match_count;
$$;

comment on function search_chunks_text(text, int) is
  'Keyword fallback over the GIN full-text index, for terms (tickers, '
  'exact figures, proper nouns) vector similarity alone tends to miss.';
