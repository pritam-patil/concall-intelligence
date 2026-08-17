-- Hybrid retrieval: reciprocal rank fusion (RRF) over the existing vector
-- search (match_chunks/match_chunks_filtered — chunks.embedding, HNSW,
-- cosine) and the existing full-text search (search_chunks_text —
-- chunks.content_tsv, GIN, already added by init_schema.sql). Both
-- channels already existed; this migration only adds the RPC that
-- combines them, plus the same three optional filters
-- match_chunks_filtered has (used by web/src/app/api/search/route.ts).
--
-- WHY RRF, not a raw score blend: cosine similarity (0..1-ish, dense) and
-- ts_rank (an unbounded, corpus-frequency-dependent float) are not on
-- comparable scales — averaging them directly would let whichever score
-- happens to have the larger typical magnitude dominate regardless of
-- actual relevance. RRF sidesteps this by fusing RANKS, not raw scores:
-- score = fusion_weight / (k + vector_rank) + (1 - fusion_weight) / (k + text_rank),
-- where a chunk absent from one channel's candidate list simply
-- contributes 0 for that term instead of needing a rank. k = 60 is the
-- standard RRF constant (Cormack et al., 2009) — high enough that rank 1
-- vs rank 5 in one list doesn't swing the fused score wildly, which is
-- the point of fusing in the first place. Not exposed as a knob: the two
-- things actually worth tuning per-deployment are how much to trust
-- vector vs. text (fusion_weight) and how many results to return
-- (match_count) — both exposed via env at the call site (HYBRID_FUSION_WEIGHT,
-- HYBRID_TOP_K — see web/.env.example, ingest/.env.example), passed
-- through as this function's parameters. See ingest/NOTES.md for a real
-- vector-only vs. hybrid comparison on five sample queries.
--
-- Candidate pool per channel (CANDIDATE_MULTIPLIER * match_count, floor
-- 40): each channel needs enough candidates that a chunk ranked outside
-- the OTHER channel's window still gets its actual rank rather than being
-- silently truncated to "not present" -- too small a pool understates
-- agreement between channels, not just recall.

create or replace function match_chunks_hybrid(
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
  'actually surfaced a given result. See ingest/NOTES.md for the '
  'vector-only vs. hybrid comparison this was built to run.';
