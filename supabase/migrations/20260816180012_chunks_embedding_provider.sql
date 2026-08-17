-- Tracks which EmbeddingsProvider computed a chunk's embedding.
--
-- Without this, a resumable embed job (ingest/src/ingest/embed.py) can't
-- tell "already embedded with the provider I'm running now" (safe to
-- skip) apart from "embedded with a DIFFERENT provider" (a different
-- vector space entirely — cosine distance between a Cloudflare bge vector
-- and a Gemini vector is meaningless, even though both are vector(768))
-- from "not embedded yet" (needs work either way). All three are silently
-- indistinguishable if all you can see is "embedding IS NOT NULL".
--
-- Nullable: existing rows (text stored, not yet embedded) have no
-- provider yet, by definition.

alter table chunks add column embedding_provider text;

comment on column chunks.embedding_provider is
  'Which EmbeddingsProvider computed embedding, e.g. "cloudflare_bge" or '
  '"gemini" (ingest/src/ingest/providers/embeddings.py). Null until '
  'embedded. A resume/backfill run only treats a row as already done when '
  'both embedding IS NOT NULL and this matches the provider it is '
  'currently running — see ingest/src/ingest/embed.py.';
