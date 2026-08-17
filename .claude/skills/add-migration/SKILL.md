---
name: add-migration
description: Write a new Supabase SQL migration under supabase/migrations/, verify it for real against a local Postgres+pgvector+PostgREST stand-in, and document what's actually verified vs. still blocked on the hosted project.
---

# Adding a Supabase migration

## Naming and location

`supabase/migrations/<UTC-timestamp>_<snake_case_description>.sql`, matching
the existing three:

- `20260816162557_init_schema.sql` — base schema (`companies`, `documents`,
  `chunks`, `doc_type` enum, `match_chunks` RPC, hnsw + gin indexes)
- `20260816180012_chunks_embedding_provider.sql` — added
  `embedding_provider` to `chunks`
- `20260816183358_match_chunks_filtered.sql` — added
  `match_chunks_filtered` RPC for `/api/search`

Generate the timestamp for real (`date -u +%Y%m%d%H%M%S`), don't hand-pick
one — migrations apply in filename order.

## Write it additively

Never edit an already-applied migration file. A schema change is a new
migration file, even a one-line one — see `chunks_embedding_provider.sql`:
one column addition, its own migration, because the base schema migration
had already been verified/applied by that point.

## Verify for real before considering it done

Use the [local-supabase-stack](../local-supabase-stack/SKILL.md) skill to
stand up Postgres+pgvector+PostgREST, apply every migration in order
including the new one, and confirm:

- It applies with no error against a **fresh** database — not a database
  that already had a hand-patched version of the same change.
- Any new index actually has the type intended — check `\di+`, don't infer
  from "no error". This project deliberately uses `hnsw` over `ivfflat` (no
  `lists` parameter to mis-tune on an empty table); a stray `ivfflat` is a
  sign of a copy-pasted template, not a real decision.
- Any new RPC is visible in PostgREST's schema cache (`NOTIFY pgrst,
  'reload schema'`, or restart PostgREST, then confirm the RPC count went
  up) and returns a real row for a real inserted test chunk — not just
  "created without error".

## Document, and be explicit about what's still unverified

The migration SQL being tested against the local stand-in is **not** the
same as it being live on the hosted project — `supabase link` / `db push`
is still blocked on `SUPABASE_ACCESS_TOKEN` plus the project's DB password
(see [`ingest/README.md`](../../../ingest/README.md) §"Connecting to
Supabase"). Say so plainly in commit messages and notes — e.g. "verified
locally against Postgres 17 + pgvector, not yet pushed to the hosted
project" — never imply the hosted schema is current when it isn't.
