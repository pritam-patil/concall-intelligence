---
name: local-supabase-stack
description: Stand up a local Postgres+pgvector+PostgREST stack as a verification stand-in for the real (still-unlinked) hosted Supabase project, so migrations, ingest/ writes, and web/ API routes can be tested against a real database over real HTTP instead of assumed to work.
---

# Local Supabase stand-in

Use this whenever something needs to be **verified for real** — a new
migration, `ingest/` writes, or a `web/` API route — and the hosted
Supabase project isn't linked (see [ingest/README.md](../../../ingest/README.md)
§"Connecting to Supabase" — `supabase link` needs `SUPABASE_ACCESS_TOKEN`
plus the project's DB password, neither present in this environment).
Never assume schema/RPC/route behavior; stand this up and hit it.

This is not `supabase start` (needs Docker, runs the whole platform stack).
It's a lighter Postgres 17 + pgvector + standalone `postgrest` binary combo
that has been used repeatedly across this project's sessions and works.

## Steps

1. **Postgres.** `brew install postgresql@17 pgvector` if not already
   present (pgvector's Homebrew formula links against the active `postgresql`
   version — check before assuming a reinstall is needed). Init a throwaway
   data dir on a non-default port so nothing real gets clobbered:
   ```bash
   initdb -D /tmp/pg-local-stack -U postgres
   pg_ctl -D /tmp/pg-local-stack -l /tmp/pg-local-stack.log -o "-p 5544" start
   createdb -p 5544 -U postgres concall_local
   ```

2. **Schema.** Apply every migration in order, then the seed:
   ```bash
   for f in supabase/migrations/*.sql; do
     psql -p 5544 -U postgres -d concall_local -f "$f"
   done
   psql -p 5544 -U postgres -d concall_local -f supabase/seed.sql
   ```
   Confirm the vector index is actually `hnsw`, not `ivfflat` — check with
   `\di+` in psql, don't infer from "no error". HNSW was a deliberate
   choice (no `lists` parameter to mis-tune on an empty table); a stray
   `ivfflat` showing up means a stale/copy-pasted migration, not a real
   decision. Confirm the full-text index is `gin` the same way.

3. **PostgREST**, on port 3020, pointed at the local Postgres above. Needs a
   JWT secret and a `postgrest.conf` with `db-uri` set; the anon/service-role
   keys `ingest/` and `web/` use are just HS256 JWTs signed with that secret
   (`{"role": "service_role"}` claim for the service-role key both packages'
   server-side clients use).

4. **The `/rest/v1` gap.** Both `supabase-py`'s `create_client()` and
   `supabase-js`'s `createClient()` unconditionally assume Kong-gateway
   routing (`/rest/v1/*`), which a bare PostgREST instance doesn't have.
   - **Python** (`ingest/`, one-off verification scripts): sidestep it —
     use `postgrest.SyncPostgrestClient(url, headers=...)` pointed straight
     at PostgREST's root instead of `supabase.create_client()`. Fine for
     verification scripts; not what the shipped `ingest/src/ingest/db.py`
     uses in production.
   - **TypeScript** (`web/`): the *shipped* route code
     (`web/src/lib/supabase.ts`) uses the real `createClient()` and must be
     exercised unmodified — run `rest_proxy.py` (in this skill's directory)
     on port 3021, forwarding `/rest/v1/*` → PostgREST root on 3020, and
     point `NEXT_PUBLIC_SUPABASE_URL` at `http://127.0.0.1:3021` for the
     test run: `python3 rest_proxy.py &`.

5. **Point the app at it.** Back up the real env file first
   (`cp web/.env.local /tmp/env.local.backup`), write local-stack values,
   run the test, then restore and `diff` to confirm exact restoration
   *before* deleting the backup or moving on. Never leave local-stack
   credentials sitting in a committed or long-lived env file.

6. **Teardown.** Kill PostgREST and the proxy, `pg_ctl -D /tmp/pg-local-stack
   stop`, remove the throwaway data dir. Confirm the `.env.local` diff was
   empty before considering cleanup done.

## Known-good specifics from prior verification runs

- Storage bucket `filings` is the **real, hosted** Supabase Storage bucket
  (not part of this local stand-in) and is genuinely populated from prior
  sessions — `download.py` sets `x-upsert: true` because re-running against
  files already uploaded is common; a `409 Duplicate` without it is
  expected, not a bug.
- `chunks.embedding_provider` must be checked before treating a chunk as
  "already embedded" during a resumable run — a chunk embedded by a
  different provider (e.g. the Gemini fallback) is not equivalent to a
  `cloudflare_bge` embedding for ranking, so resumability keys off
  `(chunk missing OR embedding_provider mismatch)`, not just presence of a
  vector.
