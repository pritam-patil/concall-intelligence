# Data source spike — findings

**Date:** 2026-08-06 (NSE Capital Market closed; `tradeDate` 06-Aug-2026 15:30 IST)
**Method:** `/spike` debug route on the deployed Worker (`nse-mcp.pritampatil.workers.dev`), ~39 invocations across 4 deployed versions.
**Scope limit:** browser-like headers and a cookie warm-up only. No scraping workarounds, no proxies, no browser automation. Where an endpoint refuses under those terms, it is recorded as unavailable rather than worked around.

## Results

| #   | Source                                                    | Status  | Consistency | Latency    | Verdict         |
| --- | --------------------------------------------------------- | ------- | ----------- | ---------- | --------------- |
| a   | `nseindia.com/api/quote-equity?symbol=RELIANCE`           | **403** | 22/22 runs  | 22–85 ms   | **Unavailable** |
| b   | `nseindia.com/api/corporate-announcements?index=equities` | **200** | 22/22 runs  | 180–200 ms | **Usable**      |
| d   | `nseindia.com/api/marketStatus`                           | **200** | 12/12 runs  | 25–135 ms  | **Usable**      |
| c   | `query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS`   | **200** | 22/22 runs  | 60–70 ms   | **Usable**      |

`d` was not in the original brief. It was added after `a` failed, because it is the endpoint the existing `get_market_status` tool actually needs.

## What the cookie warm-up does: nothing

The warm-up (`GET https://www.nseindia.com/` with navigation headers, reusing `Set-Cookie` on the API call) was tested against a no-cookie control on every run.

- The warm-up **itself** succeeds only about **1 run in 3** (8/23 returned 200). On success it yields the Akamai bot cookies `_abck`, `ak_bmsc`, `bm_mi`, `bm_sz` plus `AKA_A2`; on failure, only `AKA_A2`.
- **Cookies changed no outcome anywhere.** `a` returned 403 with and without them; `b` and `d` returned 200 with and without them. Identical across all runs.

**Recommendation: drop the warm-up.** It adds ~107 ms and a subrequest to every call, fails two times in three, and buys nothing measurable.

## Why (a) fails, and why it is not a Cloudflare problem

`quote-equity` returns an Akamai `Access Denied` HTML page, not JSON. The natural theory is that Cloudflare's egress IPs are blocked. That theory is wrong — I tested the same endpoints from a residential IP:

| Endpoint                       | From Worker (MRS) | From residential IP |
| ------------------------------ | ----------------- | ------------------- |
| `/api/quote-equity`            | 403               | **403**             |
| `/api/marketStatus`            | 200               | 200                 |
| `/api/corporate-announcements` | 200               | 200                 |

The gating is **per-endpoint, not per-client**. `quote-equity` sits behind a stricter Akamai policy than its siblings under the same `/api/` prefix. Moving the Worker, changing egress, or retrying from elsewhere will not fix it; only a full browser session would, which is outside the rules of this spike.

This is a useful negative result: it closes off a line of debugging that would otherwise have looked promising.

## Recommendation per data type

### 1. Market status → **NSE `/api/marketStatus`** (primary), Yahoo (fallback)

Returns per-segment state directly:

```json
{
	"marketState": [
		{
			"market": "Capital Market",
			"marketStatus": "Closed",
			"tradeDate": "06-Aug-2026 15:30",
			"index": "NIFTY 50",
			"last": 24636,
			"variation": 11.35,
			"percentChange": 0.05,
			"marketStatusMessage": "..."
		}
	]
}
```

This is authoritative, covers multiple segments, and needs no header tricks. It replaces the hardcoded stub in `get_market_status` as-is.

Fallback: derive from Yahoo's `meta.currentTradingPeriod.regular.start/end` (epoch, with `gmtoffset: 19800`) versus current time. Less authoritative — it is a schedule, not a live state, so it cannot see unscheduled halts.

### 2. Live / recent quotes → **Yahoo Finance chart v8**

`a` is unavailable, so Yahoo is the only viable quote source found. `meta` carries everything a quote tool needs:

`regularMarketPrice`, `previousClose`, `chartPreviousClose`, `regularMarketDayHigh`, `regularMarketDayLow`, `regularMarketVolume`, `fiftyTwoWeekHigh`, `fiftyTwoWeekLow`, `currency` (INR), `exchangeName` (NSI), `shortName`/`longName`, `regularMarketTime`, `exchangeTimezoneName` (Asia/Kolkata).

Symbol convention: NSE tickers take a `.NS` suffix (`RELIANCE` → `RELIANCE.NS`).

**Do not use Yahoo `v7/finance/quote`** — it returns **401**, it is crumb/cookie gated. The v8 chart endpoint is not.

### 3. Corporate announcements → **NSE `/api/corporate-announcements`**

Works reliably and returns live data — observed timestamps were minutes old (`an_dt: "06-Aug-2026 22:04:15"` at time of probe). 20 records per response, rotating between distinct bodies across runs (three distinct payloads seen), consistent with normal edge-cache variation rather than a frozen response.

Fields: `symbol`, `sm_name`, `desc`, `an_dt`, `sort_date`, `attchmntText`, `attchmntFile` (PDF URL on `nsearchives.nseindia.com`), `attFileSize`, `smIndustry`, `sm_isin`, `orgid`, `seq_id`, `hasXbrl`.

### 4. Historical / OHLC → **Yahoo chart v8** with `range` + `interval`

Same endpoint; `meta.validRanges` and `dataGranularity` advertise what is supported. Not separately probed in this spike.

### 5. Corporate actions → **NSE `/api/corporates-corporateActions`** (probed Burse 7, re-probed from the Worker Burse 8)

Reachable and returning data. Per-symbol (`?index=equities&symbol=RELIANCE`) it returns ~20 records, newest first, ~6KB. Fields: `symbol`, `comp`, `subject` (free text, e.g. "Dividend - Rs 6 Per Share", "Bonus 1:1", "Rights 1:15 @ Premium Rs 1247", "Demerger"), `exDate`, `recDate`, `series`, `faceVal`, `isin`, plus book-closure (`bcStartDate`/`bcEndDate`) and no-delivery (`ndStartDate`/`ndEndDate`) windows, with `-` as the absent-date placeholder. Dates are date-only `dd-Mon-yyyy`. Action type has to be inferred from the `subject` text — there is no type field.

**Worker-egress probe (Burse 8):** re-checked from Cloudflare egress the same way the original spike checked its siblings (browser UA + Referer, via a `wrangler dev --remote` fetcher). Result: **HTTP 200, 20 valid records** for RELIANCE — corporate actions is reachable from the deployment environment, not just from a residential IP. So unlike NSE `quote-equity` (403 everywhere), this endpoint works from the Worker. `get_corporate_actions` is therefore live.

### 6. Historical OHLC → **Yahoo chart v8** `?range=&interval=` (built Burse 8)

The chart endpoint carries `timestamp[]` and `indicators.quote[0].{open,high,low,close,volume[]}`. `meta.validRanges` lists the accepted ranges; `meta.dataGranularity` reports the granularity actually returned. Valid range/interval combos are constrained (minute granularities only reach back days, not years — e.g. `1m` ≈ 7 days) — Yahoo rejects bad combos, and `get_price_history` also validates up front. Bars with a null close (holidays/halts) appear and are dropped. Results are capped at ~60 rows (downsampled). Cached for 1 hour, since historical series change slowly.

### Per-symbol announcements have a full-history footgun

`/api/corporate-announcements?index=equities&symbol=SYM` **with no date range returns the symbol's entire history** — RELIANCE was ~2.8MB. Bounding it with `from_date`/`to_date` (format `dd-mm-yyyy`) collapses it to tens of KB (~90 days ≈ 37KB). The `get_announcements` tool always sends a date window for symbol queries for this reason. The market-wide feed (no symbol) stays ~13–15KB.

## Risks to design around

- **Both sources are undocumented and unofficial.** Neither NSE's `/api/` routes nor Yahoo's `query1` endpoints carry a stability guarantee. `quote-equity` being 403 today is itself evidence that NSE's posture shifts; anything usable now can close later.
- **Treat every source as failure-prone.** Cache responses, set explicit fetch timeouts, and return a typed "source unavailable" rather than letting a tool call hang or throw.
- **Yahoo rate-limits aggressively, and escalates.** Discovered in Burse 3 while capturing fixtures, not in the original spike. From a residential IP, repeated chart requests earn `429 Too Many Requests` within a handful of calls, and the block then widens: one capture needed 25 retries over 12 minutes before succeeding, and `query2` shares the limit with `query1`. The spike's 22 clean runs came from Cloudflare egress, which was not throttled — so **Worker-side behaviour and local-dev behaviour differ sharply here**. Expect 429s when developing locally, cache aggressively, and treat a 429 as retryable-but-backed-off rather than as an outage. NSE's endpoints showed no comparable throttling.
- **One transient empty response** in ~39 requests (not reproduced in 22 subsequent runs). Budget for retries.
- **Geographic caveat:** every run was served from colo **MRS (Marseille)** — Cloudflare's choice, not selectable. NSE responses may differ from an Indian colo. The residential-IP cross-check above partly offsets this for `a`, but `b`/`d` latency in particular could vary.
- **Deploy propagation is not instant.** Three requests immediately after a deploy hit the previous version. Allow ~30s before trusting post-deploy probes.

## Forward note — Project 2 (filings RAG) ingestion

The corporate-announcements endpoint doubles as the RAG's document-discovery
feed: each record carries `attchmntFile` (PDF on `nsearchives.nseindia.com`),
`desc`, `an_dt`, `symbol`, and `sm_isin`. Filtering `desc`/`attchmntText` for
transcript and annual-report announcements gives the nightly "what's new"
check (RAG burst 18) without scraping any listing pages.

Untested here: (1) whether `nsearchives.nseindia.com` PDF downloads succeed,
and from where — RAG ingestion runs from local/GitHub Actions, not the
Worker, so probe from there; (2) pagination/date-range params on the
announcements endpoint beyond the default 20 records. Both are Project 2
burst 2 items.

## Cleanup

Done in Burse 3: `/spike` and `src/spike.ts` were deleted and the removal deployed; the route now 404s. The findings above stand on the fixtures captured under `test/fixtures/`, which the mapper unit tests assert against.
