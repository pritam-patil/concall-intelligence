"""Shared NSE HTTP session handling.

Ported from nse-assist's data/upcoming.py (nse_session()/probe()) — see that
module's own docstring for how each behavior here was discovered:

  - NSE requires a GET against a section page before the API/archive hosts
    will answer (the nsit cookie) — the bare homepage now 403s outright.
  - An unprimed session gets HTTP 200 with an EMPTY body, not an error
    status; probe() treats that as a failure and retries once.
  - The API host answers brotli-compressed when Accept-Encoding offers br,
    and `requests` cannot decode brotli without an extra package — the
    response then looks like a 200 with binary garbage. API calls therefore
    send Accept-Encoding: gzip, deflate explicitly.

This was previously duplicated between nse-assist and this repo's own
scripts/probe_nse_access.py. This module is now the one copy: pipeline code
(download.py, ...) and the probe script both import it, so the two can't
drift apart the way the duplicate did the moment either changed.
"""

from __future__ import annotations

import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

REQUEST_TIMEOUT_SECONDS = 20

PRIME_URL = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements"
ANNOUNCEMENTS_REFERER = PRIME_URL
ANNUAL_REPORTS_URL = "https://www.nseindia.com/api/annual-reports"
ANNUAL_REPORTS_REFERER = (
    "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
)

# Pacing toward nsearchives.nseindia.com. SOURCES.md §1 (Project 2 burst 2)
# found no rate-limiting from either a residential IP or GitHub Actions
# egress at this pace, in either direction — this is not a measured
# threshold, just the polite pace that probe already used and that worked
# cleanly. Downloading more files is not a reason to push harder against
# someone else's free infrastructure.
DOWNLOAD_PACE_SECONDS = 1.5


def nse_session() -> requests.Session:
    """A requests Session primed with NSE's cookies. Build once and reuse."""
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.get(PRIME_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    return session


def probe(session, name, url, params, referer):
    """{name, status, rows, error}. Empty-body 200s count as failures and get
    one retry — that is NSE's way of saying the cookies did not take."""
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Referer": referer,
    }
    outcome = {"name": name, "status": None, "rows": None, "error": None}
    for attempt in (1, 2):
        try:
            response = session.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            outcome["error"] = str(exc)
            continue
        outcome["status"] = response.status_code
        if not response.ok or not response.text.strip():
            outcome["error"] = (
                f"HTTP {response.status_code}" if not response.ok else "empty body"
            )
            continue
        try:
            payload = response.json()
        except ValueError as exc:
            outcome["error"] = f"not JSON ({exc})"
            continue
        outcome["rows"] = payload if isinstance(payload, list) else payload.get("data", [])
        outcome["error"] = None
        break
    return outcome


class FetchError(Exception):
    """A binary fetch (fetch_binary) failed — bad status, empty body, or a
    body that isn't actually the file type expected (NSE's bot-detection
    page is HTML with a 200 status, which would otherwise look like
    success)."""


def fetch_binary(session, url, *, referer=None, timeout=REQUEST_TIMEOUT_SECONDS):
    """GET a binary file (a filing PDF) through a primed session.

    Redirects are followed — `requests`' default, left enabled rather than
    reimplemented, since NSE does occasionally serve a filing via a 30x (a
    document moved between a filing and a revision). Returns
    (content: bytes, final_url: str) so a caller can tell when that
    happened; final_url differs from `url` only on a redirect.
    """
    headers = {"Accept": "application/pdf,*/*", "Accept-Encoding": "gzip, deflate"}
    if referer:
        headers["Referer"] = referer
    try:
        response = session.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise FetchError(f"network error: {exc}") from exc
    if not response.ok:
        raise FetchError(f"HTTP {response.status_code}")
    content = response.content
    if not content:
        raise FetchError("empty body")
    content_type = response.headers.get("Content-Type", "")
    if not (content[:4] == b"%PDF" or content_type.startswith("application/pdf")):
        raise FetchError(
            f"not a PDF (content-type={content_type!r}, first bytes={content[:16]!r})"
        )
    return content, response.url
