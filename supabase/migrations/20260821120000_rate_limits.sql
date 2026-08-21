-- Per-IP daily query cap for the public query endpoints (/api/ask, /api/search)
-- — an abuse guardrail. One row per (ip, day); rate_limit_hit() records a hit
-- atomically and reports whether the caller is still under the limit. Called
-- with the service-role key from web/src/lib/guard.ts.

create table if not exists rate_limits (
  ip    text not null,
  day   date not null,
  count integer not null default 0,
  primary key (ip, day)
);

comment on table rate_limits is
  'Per-IP daily request counter for the public query endpoints. `day` is UTC. '
  'Rows accumulate; prune old days with a scheduled delete if it ever matters.';

-- Only the server (service-role, which bypasses RLS) touches this table.
-- Enabling RLS with no policies denies anon/authenticated access outright.
alter table rate_limits enable row level security;

-- Atomic "record a hit, tell me if it's allowed". A single INSERT ... ON
-- CONFLICT DO UPDATE keeps the increment race-free even under concurrent
-- requests; `allowed` is the POST-increment count still being within the
-- limit, so exactly `p_limit` hits per (ip, day) are allowed and the
-- (p_limit + 1)-th is refused.
create or replace function rate_limit_hit(p_ip text, p_day date, p_limit integer)
returns table (allowed boolean, used integer, lim integer)
language sql
as $$
  with hit as (
    insert into rate_limits (ip, day, count)
    values (p_ip, p_day, 1)
    on conflict (ip, day) do update set count = rate_limits.count + 1
    returning count
  )
  select (hit.count <= p_limit) as allowed, hit.count as used, p_limit as lim
  from hit;
$$;

comment on function rate_limit_hit is
  'Increment (ip, day)''s counter and return {allowed, used, lim}. allowed is '
  'the post-increment count <= p_limit, so p_limit hits/day are permitted.';
