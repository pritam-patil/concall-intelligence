-- sanity.sql — post-ingest sanity checks: chunk counts per document,
-- average tokens per chunk, and one real similarity query.
--
--   psql "$DATABASE_URL" -v query_embedding="'[...]'" -f sanity.sql
--
-- The similarity query (§3) needs a real query embedding — pgvector can't
-- compute one from SQL alone, since that means calling the embeddings API
-- this project uses (same requirement match_chunks() has everywhere else
-- it's called — see web/src/app/api/ask/route.ts for the other caller).
-- Generate one with the SAME provider the chunks were embedded with
-- (EMBEDDINGS_PROVIDER — mixing providers here gives meaningless cosine
-- distances, same as everywhere else in this project) and pass it in via
-- -v:
--
--   python3 -c "
--   from ingest.config import get_settings
--   from ingest.providers.embeddings import get_embeddings_provider
--   provider = get_embeddings_provider(get_settings())
--   vec = provider.embed(['management commentary on margins'])[0]
--   print('[' + ','.join(str(x) for x in vec) + ']')
--   "
--
-- See ingest/NOTES.md for real output from a real ingest run.


-- 1. Chunk counts per document.
select
  d.symbol,
  d.doc_type,
  d.period,
  count(c.id) as chunk_count
from documents d
join chunks c on c.document_id = d.id
group by d.id, d.symbol, d.doc_type, d.period
order by d.symbol, d.doc_type;


-- 2. Average tokens per chunk — overall, then per document (the overall
-- figure alone hides that annual-report chunks and concall chunks are
-- built from very differently shaped source text).
select round(avg(token_count), 1) as avg_tokens_per_chunk_overall
from chunks;

select
  d.symbol,
  d.doc_type,
  round(avg(c.token_count), 1) as avg_tokens_per_chunk,
  min(c.token_count) as min_tokens,
  max(c.token_count) as max_tokens
from documents d
join chunks c on c.document_id = d.id
group by d.id, d.symbol, d.doc_type
order by d.symbol, d.doc_type;


-- 3. "management commentary on margins" -> top 5 chunks by cosine
-- similarity, with symbol and page (match_chunks already joins chunks to
-- documents for exactly this — see supabase/migrations/*_init_schema.sql).
select
  symbol,
  page,
  section,
  round(similarity::numeric, 4) as similarity,
  left(content, 200) as content_preview
from match_chunks(:'query_embedding'::vector, 5);
