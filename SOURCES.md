# SOURCES

Measured access findings and curated seed URLs for the ingestion pipeline.
Everything below is from live probes, not documentation — see
[`ingest/scripts/probe_nse_access.py`](ingest/scripts/probe_nse_access.py) for
the exact code that produced it, and `ingest/results/*.json` for raw output.

## 1. PDF access — the unknown, closed

**Question:** `nsearchives.nseindia.com` has served exactly two things across
this codebase's prior projects (nse-assist, nse-mcp) — the daily bhavcopy CSV
zip and the Nifty500 constituent list — never a filing PDF. Does the same
nsit-cookie session that unlocks the JSON API also unlock PDF downloads from
that host? Untested until now — [`data/DATA_SOURCES.md`](data/DATA_SOURCES.md)
(the nse-mcp Worker spike that scoped this project) names it explicitly under
"Untested here": *"whether `nsearchives.nseindia.com` PDF downloads succeed,
and from where — RAG ingestion runs from local/GitHub Actions, not the
Worker, so probe from there."* That spike also adds a third environment data
point to the two measured below: `corporate-announcements` answered 200 from
Cloudflare Worker egress (colo MRS) too, on the same header/cookie terms —
this is exactly why probing PDF access specifically, not re-probing the JSON
API, was the actual gap.

**Method:** the `nse_session()`/`probe()` pattern from
[nse-assist/data/upcoming.py](https://github.com/pritam-patil/nse-assist/blob/main/data/upcoming.py)
reused verbatim — section-page priming for the nsit cookie (bare homepage 403s),
`Accept-Encoding: gzip, deflate` sent explicitly (offering `br` gets a
brotli-compressed body `requests` can't decode, which looks like a 200 with
garbage), and an empty-body 200 counted as a failure with one retry. 10 real
`attchmntFile` URLs were pulled from the live announcements API for RELIANCE
and downloaded — from this machine, and separately from a throwaway GitHub
Actions workflow (private repo `pritam-patil/nse-pdf-access-probe-throwaway`
— deletion attempted, see note at the end of this section), because NSE
access is measurably location-sensitive (the Workers-egress spike in
nse-assist showed cloud egress getting empty bodies where a residential
connection got data).

**Result: 10/10 succeeded in both environments. No blocking, no rate-limit
headers, no CAPTCHA.**

| | local (residential, IN) | GitHub Actions (Azure, US) |
|---|---|---|
| egress IP | 103.197.75.44 | 172.184.239.226 |
| downloads | 10/10, HTTP 200 | 10/10, HTTP 200 |
| total bytes | 14,667,931 | 14,667,931 (byte-identical) |
| avg / min / max latency | 121ms / 30ms / 386ms | 452ms / 267ms / 1410ms |
| `Retry-After` / `X-RateLimit-*` headers | none observed | none observed |
| errors | 0 | 0 |

(Run twice — once before a script fix that trimmed a bloated results file,
once after; both runs got the identical 10/10 result, only latencies moved
with ordinary network variance. Numbers above are from the second run.)

Sizes are identical between environments (same 10 files, same bytes — sanity
check that both machines actually fetched real content, not two different
error pages of the same length). GHA is consistently slower per request
(higher fixed round-trip cost to India from Azure US egress, not throttling —
no 429s, no backoff signal, no shrinking response, just a flatter latency
floor) but equally successful. One data point each — this is not a
load test and says nothing about behavior under sustained/bulk fetching, only
that a single primed session, from either kind of network, gets real PDFs
back today.

**Conclusion:** unlike the bare `www.nseindia.com` homepage (403s outright)
and unlike the Workers-egress case in nse-assist (empty bodies), PDF downloads
from `nsearchives.nseindia.com/corporate/...` work cleanly with the existing
session-priming pattern, from both a residential IP and GitHub Actions'
cloud IP range, as of 2026-08-16. Ingestion can rely on this path; no special
handling (proxy, alternate host, slower pacing) is warranted by what was
measured. Re-probe (`python ingest/scripts/probe_nse_access.py pdf-access`)
if downloads start failing in production — access here is empirically not
guaranteed to stay this permissive.

**Note on the throwaway repo:** `gh repo delete` failed — the local `gh`
token lacks the `delete_repo` scope, and granting it needs an interactive
browser step this session won't do unattended. The private repo
`pritam-patil/nse-pdf-access-probe-throwaway` (script + workflow + one
completed run, no secrets) is still on GitHub pending manual deletion.

## 2. Seeds — per-symbol full-history announcements

**Method:** `GET /api/corporate-announcements?index=equities&symbol=<SYM>`
with no `from_date`/`to_date` — an unbounded symbol query, which returns the
entire filing history in one multi-MB response (this is the backfill
mechanism the ingestion pipeline should use for a symbol's initial load, not
an edge case to guard against). Rows are kept when `desc` or `attchmntText`
contains "transcript" or "annual report" (case-insensitive).

| Symbol | Total rows returned | Transcript/Annual Report matches |
|---|---|---|
| RELIANCE | 3,331 | 38 |
| TCS | 3,346 | 22 |
| HDFCBANK | 2,324 | 19 |
| INFY | 2,914 | 55 |
| TATAMOTORS | 0 (see below) | 0 |
| TMCV (successor) | 96 | 3 |
| TMPV (successor) | 2,783 | 36 |

**TATAMOTORS returned 0 rows cleanly (HTTP 200, no error) — not a probe
failure.** The symbol was retired in NSE's 2025 demerger of Tata Motors into
two listed entities: `TMCV` (Tata Motors Limited — commercial vehicles) and
`TMPV` (Tata Motors Passenger Vehicles Limited), confirmed two independent
ways: `search_symbol("tata motors")`, and NSE's own equity master list
([`data/EQUITY_L.csv`](data/EQUITY_L.csv)), which lists `TMCV` with listing
date `12-NOV-2025` (the newly carved-out entity) and `TMPV` carrying the
original `22-JUL-1998` listing date and ISIN `INE155A01022` (the continuing
entity — what used to trade as TATAMOTORS was renamed, not relisted). Both
successors were re-probed above and substituted as the fifth/sixth pilot
symbols. Anywhere the ingestion config lists `TATAMOTORS`, use `TMCV` and
`TMPV` instead — the old symbol is gone, not just paginated differently.

**Chosen seed URLs** (most recent transcript filing per symbol, from the
matches above — representative, not exhaustive; the full match lists are in
`ingest/results/seeds_local.json` and `seeds_tatamotors_successors.json`):

| Symbol | Filed | Description | URL |
|---|---|---|---|
| RELIANCE | 19-Jul-2026 | Transcript of the discussion on Unaudited Financial Results (Q1 FY27, analyst meet 17-Jul-2026) | https://nsearchives.nseindia.com/corporate/kavinavora_19072026180618_SE_Transcript.pdf |
| TCS | 15-Jul-2026 | Transcript intimation | https://nsearchives.nseindia.com/corporate/TCS_CORPCS_15072026193646_SEInt15072026_Signed.pdf |
| HDFCBANK | 24-Jul-2026 | Transcript of earnings call (18-Jul-2026) | https://nsearchives.nseindia.com/corporate/HDFCBANK_24072026154746_SEintimationTranscriptofearningscall18jul2026.pdf |
| INFY | 28-Jul-2026 | Earnings Call Transcript | https://nsearchives.nseindia.com/corporate/Infosys_28072026202438_SE_filing_Earnings_call_transcript.pdf |
| TMCV | 19-May-2026 | NSE/BSE filing (commercial vehicles) | https://nsearchives.nseindia.com/corporate/TMLCOMMERCIAL_19052026145257_NSEBSE.pdf |
| TMPV | 20-May-2026 | NSE/BSE transcript filing (passenger vehicles) | https://nsearchives.nseindia.com/corporate/TATAMOTORSSJS_20052026215152_NSEBSETRANSCRIPT.pdf |

All six URLs are covered by the PDF-access finding above (same host, same
`/corporate/` path shape) — not independently re-verified byte-for-byte, but
no reason expected to differ.

## 3. Annual reports — dedicated API source

**Question:** is `GET /api/annual-reports?index=equities&symbol=<SYM>` a
better source for annual reports than filtering the announcements feed?

**Result: yes, clearly better — probed live for all six pilot symbols,
17–18 rows each (one per filing year back to ~2010, `TMCV` only 1 row since
it only started existing as a symbol in 2026), every row structured
(`fromYr`, `toYr`, `fileName`, `attFileSize`) rather than parsed out of prose.
This is the primary annual-report source; the announcements-keyword filter in
§2 is a fallback only for whatever this endpoint doesn't carry.**

Latest annual report per symbol (all confirmed 200s, not yet downloaded —
size column below is NSE's own reported `attFileSize` where present):

| Symbol | Year | Size | URL |
|---|---|---|---|
| RELIANCE | FY2025-26 | 10.50 MB | https://nsearchives.nseindia.com/annual_reports/AR_29285_RELIANCE_2025_2026_A_11007429_28052026133947.pdf |
| TCS | FY2025-26 | 16.62 MB | https://nsearchives.nseindia.com/annual_reports/AR_29263_TCS_2025_2026_A_17427580_15052026234830.pdf |
| HDFCBANK | FY2025-26 | 12.08 MB | https://nsearchives.nseindia.com/annual_reports/AR_29735_HDFCBANK_2025_2026_A_12667766_11072026001055.pdf |
| INFY | FY2025-26 | 8.57 MB | https://nsearchives.nseindia.com/annual_reports/AR_29313_INFY_2025_2026_U_8985411_30052026200413.pdf |
| TMCV | FY2025-26 | 19.88 MB | https://nsearchives.nseindia.com/annual_reports/AR_29361_TMCV_2025_2026_A_20847723_06062026213355.pdf |
| TMPV | FY2025-26 | 20.45 MB | https://nsearchives.nseindia.com/annual_reports/AR_29395_TMPV_2025_2026_A_21445169_15062026215933.pdf |

Some older rows carry `.zip` filenames instead of `.pdf` (e.g. RELIANCE
FY2022-23, TCS FY2021-22/2022-23) — the parser must branch on file extension,
not assume PDF.

**Documented fallback: company investor-relations pages.** Not probed
programmatically — each company runs its own site with its own structure, no
common API, and scraping N different IR sites is out of scope for this pass.
Recorded here as the documented last resort when a symbol has no
`annual-reports` API rows (new listings, or years before NSE's own archive
starts) or when a filing is missing/corrupted at the NSE URL:

- Reliance Industries: https://www.ril.com/investors
- TCS: https://www.tcs.com/investor-relations
- HDFC Bank: https://www.hdfcbank.com/personal/about-us/investor-relations
- Infosys: https://www.infosys.com/investors.html
- Tata Motors (TMCV/TMPV): https://www.tatamotors.com/investors/

## Reproducing this

```bash
cd ingest
python3 scripts/probe_nse_access.py pdf-access --out results/pdf_access.json
python3 scripts/probe_nse_access.py seeds --out results/seeds.json
python3 scripts/probe_nse_access.py seeds --symbols TMCV,TMPV --out results/seeds_tm.json
python3 scripts/probe_nse_access.py annual-reports --symbol TCS
```

Requires only `requests` (already in `ingest/pyproject.toml`). Measured
2026-08-16 from a residential IN connection and GitHub Actions (Azure US
egress); re-run before relying on any of the above in production — NSE's
access posture is not contractually stable.
