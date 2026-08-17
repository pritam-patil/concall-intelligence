---
name: nse-fetch
description: The verified session/cookie-priming pattern for talking to NSE's website APIs (nseindia.com, nsearchives.nseindia.com) — use for any new endpoint, symbol, or document-type probing so results are real, not assumed.
---

# NSE fetch discipline

[`ingest/src/ingest/nse_fetch.py`](../../../ingest/src/ingest/nse_fetch.py)
is the one copy of this pattern (ported from a prior project, `nse-assist`,
and de-duplicated from this repo's own probe script — don't fork it a third
time). Any new NSE endpoint work — a new document type, a new symbol, a
changed API shape — goes through its `nse_session()` / `probe()` /
`fetch_binary()`, not a fresh `requests` call with hand-rolled headers.

## What's true about NSE's site, confirmed by testing (not assumed)

- A bare `requests.get()` to the homepage or an API now 403s outright — NSE
  requires priming: `GET` a section/listing page first (`PRIME_URL`) to
  receive the `nsit` cookie, before any API/archive call will answer.
- An **unprimed session doesn't error** — it returns HTTP 200 with an
  **empty body**. Treat empty-body-200 as a failure and retry once after
  re-priming, exactly like `probe()` does. A 200 status alone is not
  success.
- Request `Accept-Encoding: gzip, deflate` explicitly (not `br`) for API
  calls — `requests` can't decode brotli without an extra package, and
  NSE's API host will happily send brotli if offered, producing a 200 full
  of binary garbage that looks like a decoding bug but isn't.
- `nsearchives.nseindia.com` (the PDF/attachment host) showed no
  rate-limiting at `DOWNLOAD_PACE_SECONDS = 1.5` from either a residential
  IP or GitHub Actions egress, in either direction — measured empirically
  (`SOURCES.md` §1), not assumed. It's a *polite pace that worked cleanly*,
  not a proven ceiling — don't push it faster just because ingesting more
  documents would be convenient.

## Discipline for new probing

1. Reuse `nse_session()` / `probe()` / `fetch_binary()` verbatim — don't
   inline a new header set "just for this one endpoint".
2. Record real status codes, sizes, timing, and any refusal. Source/endpoint
   decisions go in `SOURCES.md`; pipeline-run observations go in
   `ingest/NOTES.md`. A 403 or an empty body is a **finding to document**,
   not a bug to route around with different headers, proxies, or
   user-agents.
3. Unbounded per-symbol announcement queries return the **entire filing
   history** (multi-MB responses) — that is the backfill mechanism this
   project relies on, not something to paginate around or truncate.
4. If NSE refuses something outright, stop and document the refusal rather
   than working around it — same discipline applied throughout this
   project's earlier NSE-access probing (PDF access, `/api/annual-reports`).
