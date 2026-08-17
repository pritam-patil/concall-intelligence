-- Filtered vector search for web/src/app/api/search/route.ts.
--
-- match_chunks() (baseline migration) is what /api/ask uses — no filters,
-- and its `source` column is a pre-built citation string rather than the
-- raw fields a filterable search UI needs (doc_type, period, source_url
-- separately). Rather than overload match_chunks with filter params
-- /api/ask has no use for, this is a second, purpose-built function for
-- /api/search specifically — same join, same cosine-distance ordering,
-- different output shape and three optional filters.

create or replace function match_chunks_filtered(
  query_embedding vector(768),
  match_count int default 10,
  filter_symbol text default null,
  filter_doc_type doc_type default null,
  filter_period text default null
)
returns table (
  content text,
  symbol text,
  doc_type doc_type,
  period text,
  page integer,
  source_url text,
  score float
)
language sql stable
as $$
  select
    chunks.content,
    documents.symbol,
    documents.doc_type,
    documents.period,
    chunks.page,
    documents.source_url,
    1 - (chunks.embedding <=> query_embedding) as score
  from chunks
  join documents on documents.id = chunks.document_id
  where chunks.embedding is not null
    and (filter_symbol is null or documents.symbol = filter_symbol)
    and (filter_doc_type is null or documents.doc_type = filter_doc_type)
    and (filter_period is null or documents.period = filter_period)
  order by chunks.embedding <=> query_embedding
  limit match_count;
$$;

comment on function match_chunks_filtered(vector, int, text, doc_type, text) is
  'Vector similarity search for /api/search, with optional (symbol, '
  'doc_type, period) filters — each null means "no filter on that column". '
  'Cosine distance (<=>), matching match_chunks() and bge''s intended '
  'metric. Returns raw fields (not a pre-built citation string like '
  'match_chunks().source) since /api/search''s callers filter/display on '
  'them directly.';
