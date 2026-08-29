-- documents.filed_at, and both retrieval RPCs widened to return it.
--
-- WHY: /api/ask renders each context passage with a
-- (doc_type, period, page, symbol) header, and every concall row had
-- period null — see the history in ingest/src/ingest/seeds.py. The model is
-- instructed never to use outside knowledge (system rule 1), so an undated
-- passage is one it cannot answer a fiscal-year question from: asked "what
-- did Infosys say about its FY2025-26 revenue growth guidance?" it saw
-- `period=n/a` on every transcript, could not tie the passage to a year,
-- and returned NOT_FOUND. The same question without the year answered from
-- the very same passages. That refusal was CORRECT reasoning over missing
-- metadata; the metadata is the bug.
--
-- ingest/src/ingest/period.py now derives a concall's period, and this
-- column records the filing date it was derived from. The date is not
-- decoration: a period derived from the filing date is a rule rather than a
-- reading, and surfacing the date next to it is what keeps the model's (and
-- a reader's) trust in the label proportionate to its provenance.
--
-- `date`, not `timestamptz`: NSE stamps a time into the archive filename,
-- but the useful granularity for "which quarter does this report on" is the
-- day, and a date does not invite a false precision about IST vs UTC.

alter table documents add column filed_at date;

comment on column documents.filed_at is
  'The date the filing was published on NSE. Derived from the timestamp NSE '
  'stamps into every archived filename (_ddmmyyyyHHMMSS_) rather than from '
  'the announcements feed''s an_dt, so an already-ingested row can be dated '
  'from a column it already has — verified equal to an_dt for all six pilot '
  'seeds. Null for a source_url carrying no stamp. Also the fallback signal '
  'documents.period is derived from for concalls; see ingest/period.py.';

create index documents_filed_at_idx on documents (filed_at desc nulls last);


-- --- retrieval RPCs -----------------------------------------------------------
--
-- Both gain a filed_at column. `create or replace function` cannot change a
-- function's RETURNS TABLE shape, so each is dropped first — safe, since
-- both are pure read-path functions with no dependent objects, and the
-- web/ callers deploy alongside this migration.

drop function if exists match_chunks_filtered(vector, int, text, doc_type, text);

create function match_chunks_filtered(
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
  filed_at date,
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
    documents.filed_at,
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
  'them directly. filed_at accompanies period so a caller can date a '
  'passage even where period is null.';


drop function if exists match_chunks_hybrid(vector, text, int, float, text, doc_type, text);

create function match_chunks_hybrid(
  query_embedding vector(768),
  query_text text,
  match_count int default 10,
  fusion_weight float default 0.5,
  filter_symbol text default null,
  filter_doc_type doc_type default null,
  filter_period text default null
)
returns table (
  content text,
  symbol text,
  doc_type doc_type,
  period text,
  filed_at date,
  page integer,
  source_url text,
  score float,
  vector_rank int,
  text_rank int
)
language sql stable
as $$
  with candidate_pool as (
    select greatest(match_count * 4, 40) as n
  ),
  vector_matches as (
    select
      chunks.id,
      row_number() over (order by chunks.embedding <=> query_embedding) as rnk
    from chunks
    join documents on documents.id = chunks.document_id
    where chunks.embedding is not null
      and (filter_symbol is null or documents.symbol = filter_symbol)
      and (filter_doc_type is null or documents.doc_type = filter_doc_type)
      and (filter_period is null or documents.period = filter_period)
    order by chunks.embedding <=> query_embedding
    limit (select n from candidate_pool)
  ),
  text_matches as (
    select
      chunks.id,
      row_number() over (
        order by ts_rank(chunks.content_tsv, websearch_to_tsquery('english', query_text)) desc
      ) as rnk
    from chunks
    join documents on documents.id = chunks.document_id
    where chunks.content_tsv @@ websearch_to_tsquery('english', query_text)
      and (filter_symbol is null or documents.symbol = filter_symbol)
      and (filter_doc_type is null or documents.doc_type = filter_doc_type)
      and (filter_period is null or documents.period = filter_period)
    order by rnk
    limit (select n from candidate_pool)
  ),
  fused as (
    select
      coalesce(v.id, t.id) as id,
      fusion_weight * coalesce(1.0 / (60 + v.rnk), 0)
        + (1 - fusion_weight) * coalesce(1.0 / (60 + t.rnk), 0) as score,
      v.rnk as vector_rank,
      t.rnk as text_rank
    from vector_matches v
    full outer join text_matches t on t.id = v.id
  )
  select
    chunks.content,
    documents.symbol,
    documents.doc_type,
    documents.period,
    documents.filed_at,
    chunks.page,
    documents.source_url,
    fused.score,
    fused.vector_rank::int,
    fused.text_rank::int
  from fused
  join chunks on chunks.id = fused.id
  join documents on documents.id = chunks.document_id
  order by fused.score desc
  limit match_count;
$$;

comment on function match_chunks_hybrid(vector, text, int, float, text, doc_type, text) is
  'Reciprocal rank fusion of match_chunks'' vector ranking and '
  'search_chunks_text''s keyword ranking. fusion_weight in [0,1]: 1.0 is '
  'vector-only, 0.0 is text-only, 0.5 (default) weighs both equally. '
  'vector_rank/text_rank are null when a chunk did not appear in that '
  'channel''s candidate pool at all -- useful for seeing which channel '
  'actually surfaced a given result. filed_at accompanies period so '
  '/api/ask can date a passage in the context it builds for the model. '
  'See ingest/NOTES.md for the vector-only vs. hybrid comparison this was '
  'built to run.';


-- --- backfilling existing rows -------------------------------------------------
--
-- Deliberately NOT done in SQL. The quarter rule (an Apr-Mar fiscal year, a
-- minimum lag after quarter end, an explicit "Q1FY27" beating the date)
-- lives in ingest/src/ingest/period.py and is unit-tested against the real
-- seed rows; re-expressing it here would be a second implementation free to
-- drift from the first. Run instead, after applying this migration:
--
--     uv run ingest backfill-dating          # add --dry-run to preview
--
-- It is idempotent and derives from documents.source_url alone, so it needs
-- no network access and can be re-run safely.
