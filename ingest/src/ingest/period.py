"""Deriving a filing's DATE and its REPORTING PERIOD from NSE metadata.

Why this module exists: `documents.period` was left null for every concall
(see the history in seeds.py / check_new.row_to_doc — "NSE's announcement
prose doesn't carry a reliably parseable period"). That was true of the
prose, but it made the retrieved passages *undatable*, and an undatable
passage is one the /api/ask model cannot reason about: asked for a named
fiscal year it sees `period=n/a` on every transcript, cannot tie the
passage to the year, and refuses — correctly, on rule 1 (never use outside
knowledge). Confirmed live: "What did Infosys say about its FY2025-26
revenue growth guidance?" refused, while the same question without the year
answered from the same passages.

The fix is NOT to guess harder at the prose. It is two signals, in
confidence order, both of which are structural rather than prose:

  1. An EXPLICIT period in the filing's text or filename — "Q1FY27",
     "quarter ended June 30, 2026". Only RELIANCE's boilerplate among the
     six pilot seeds carries one, but the expansion batch's filenames often
     do (AXISBANK's `...Q1FY27EarningsCall...`, SUNPHARMA's
     `...TranscriptQ1FY27_Signed`).
  2. The FILING DATE, which every row has, mapped to the most recently
     ended fiscal quarter. Indian listed companies file quarterly results
     within 45 days of quarter end and the transcript follows within days,
     so "the quarter that had already ended when this was filed" is a rule,
     not a guess. QUARTER_END_MIN_LAG_DAYS keeps a filing in the first days
     after a quarter end attributed to the PREVIOUS quarter — results for a
     quarter are never out that fast.

`filed_at` is recorded alongside, and surfaced to the model, precisely so
that a period derived by rule (2) is never the only thing the answer rests
on: the reader and the model both see the date the derivation came from.

Filing dates come from the SOURCE URL, not the announcements row: NSE
timestamps every archived filename `_ddmmyyyyHHMMSS_`, which means an
already-ingested `documents` row can be backfilled from a column it already
has, with no re-query of NSE. Verified against the announcements feed's own
`an_dt` for all six pilot seeds (INFY: URL 28072026202438 vs an_dt
"28-Jul-2026 20:24:53" — the same filing, 15s between upload and
announcement).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# A filing in the first days after a quarter end reports on the quarter
# BEFORE it — nobody publishes quarterly results within a week of the close.
# 10 days is comfortably below the real gap (the earliest of the seeds is
# TCS at 15 days after 30-Jun) and comfortably above zero.
QUARTER_END_MIN_LAG_DAYS = 10

# NSE archives every filing as `<prefix>_<ddmmyyyyHHMMSS>_<name>.pdf`. Bounded
# by non-digits so the 14-digit stamp is not confused with the shorter numeric
# runs that share these filenames (`AR_29313_INFY_2025_2026_U_8985411_...`).
_URL_STAMP = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{4})\d{6}(?!\d)")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "Q1FY27", "Q1 FY 2026-27", "Q3 FY2027" — a quarter number bound to a fiscal
# year in one token run. The year group is normalised by _fiscal_year_end.
# The boundaries are deliberately NOT \b: the richest source of explicit
# periods is filenames, where the token is welded into a camel-case run
# ("...TranscriptQ1FY27_Signed") — so the leading edge also accepts a
# lowercase-to-Q transition, and the trailing edge only has to rule out a
# further digit ("_" after "FY27" is a word character, so \b would not match).
_EXPLICIT_QUARTER = re.compile(
    r"(?:(?<![A-Za-z0-9])|(?<=[a-z]))Q([1-4])\s*[-/]?\s*FY\s*'?"
    r"((?:\d{4}|\d{2})(?:\s*[-/]\s*\d{2,4})?)(?!\d)",
    re.IGNORECASE,
)

# "quarter ended June 30, 2026" / "quarter ended 30 June 2026" / "...30.06.2026".
_QUARTER_ENDED = re.compile(
    r"quarter\s+(?:and\s+\w+\s+)?ended\s+(?:on\s+)?"
    r"(?:(?P<m1>[A-Za-z]{3,9})\.?\s+(?P<d1>\d{1,2})|(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m2>[A-Za-z]{3,9}))"
    r"[,\s]+(?P<y>\d{4})",
    re.IGNORECASE,
)


def filed_at_from_url(url: str) -> date | None:
    """The filing date NSE stamped into an archived filename, or None if the
    URL carries no usable stamp (never seen on nsearchives, but a
    non-archive URL must not crash a backfill)."""
    filename = url.split("?", 1)[0].rsplit("/", 1)[-1]
    for day, month, year in _URL_STAMP.findall(filename):
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            continue  # a 14-digit run that isn't a date — keep looking
    return None


def _fiscal_year_end(raw: str) -> int:
    """The calendar year an Indian fiscal year ENDS in, from the year part of
    an "FY..." token. FY27 / FY2027 / FY2026-27 / FY26-27 all mean the year
    ending 31-Mar-2027, so all four return 2027."""
    parts = [p for p in re.split(r"[-/\s]+", raw.strip()) if p]
    tail = parts[-1]
    return int(tail) if len(tail) == 4 else 2000 + int(tail)


def quarter_label(quarter: int, fiscal_year_end: int) -> str:
    """The `documents.period` spelling for concalls, per that column's comment
    in the schema: "Q1 FY27"."""
    return f"Q{quarter} FY{fiscal_year_end % 100:02d}"


def label_for_quarter_end(quarter_end: date) -> str:
    """The period label for the fiscal quarter ENDING on `quarter_end`
    (31-Mar / 30-Jun / 30-Sep / 31-Dec). India's fiscal year runs Apr-Mar, so
    the quarter ending 30-Jun-2026 is Q1 of FY2026-27 -> "Q1 FY27", and the
    one ending 31-Mar-2026 is Q4 of FY2025-26 -> "Q4 FY26"."""
    quarter = {6: 1, 9: 2, 12: 3, 3: 4}[quarter_end.month]
    year_end = quarter_end.year + 1 if quarter_end.month >= 4 else quarter_end.year
    return quarter_label(quarter, year_end)


def _preceding_quarter_end(day: date) -> date:
    """The latest fiscal quarter end at least QUARTER_END_MIN_LAG_DAYS before
    `day` — the quarter a filing on that day reports on."""
    cutoff = day - timedelta(days=QUARTER_END_MIN_LAG_DAYS)
    ends = [date(cutoff.year, 3, 31), date(cutoff.year, 6, 30),
            date(cutoff.year, 9, 30), date(cutoff.year, 12, 31)]
    past = [e for e in ends if e <= cutoff]
    return past[-1] if past else date(cutoff.year - 1, 12, 31)


def period_from_text(text: str) -> str | None:
    """An EXPLICIT period stated in filing text or a filename, or None. This
    is signal (1) — preferred over the filing date whenever it is present."""
    explicit = _EXPLICIT_QUARTER.search(text)
    if explicit:
        return quarter_label(int(explicit.group(1)), _fiscal_year_end(explicit.group(2)))

    ended = _QUARTER_ENDED.search(text)
    if ended:
        month_name = (ended.group("m1") or ended.group("m2")).lower()[:3]
        month = _MONTHS.get(month_name)
        # Only a real fiscal quarter end counts; "quarter ended August 31"
        # is not one of ours and is better left to the filing date.
        if month in (3, 6, 9, 12):
            try:
                return label_for_quarter_end(date(int(ended.group("y")), month, int(ended.group("d1") or ended.group("d2"))))
            except (ValueError, KeyError):
                return None
    return None


def period_from_filing_date(filed_at: date) -> str:
    """Signal (2): the quarter that had already ended when this was filed."""
    return label_for_quarter_end(_preceding_quarter_end(filed_at))


def derive_concall_period(
    source_url: str,
    text: str = "",
    filed_at: date | None = None,
) -> tuple[str | None, date | None, str]:
    """`(period, filed_at, how)` for a concall filing.

    `how` names the signal used ("text", "filed_at", or "none") so an
    ingestion log line says which rows were derived by rule rather than
    read off the filing — the auditability that makes deriving a period at
    all defensible. `text` should be everything prose-ish about the filing:
    the announcement `desc`, its `attchmntText`, and the filename.
    """
    if filed_at is None:
        filed_at = filed_at_from_url(source_url)

    explicit = period_from_text(f"{text} {source_url}")
    if explicit:
        return explicit, filed_at, "text"
    if filed_at is not None:
        return period_from_filing_date(filed_at), filed_at, "filed_at"
    return None, None, "none"
