"""Backfill `documents.filed_at` (and, for concalls, `documents.period`) on
rows ingested before those were derived.

    uv run ingest backfill-dating --dry-run   # show what would change
    uv run ingest backfill-dating

Runs entirely off `documents.source_url`: NSE stamps the filing date into
every archived filename, so no announcement row needs re-fetching and no
network access is required. Idempotent — a second run finds nothing to do —
and it never overwrites a period a row already has, so the annual reports'
real "FY2025-26" (structured, straight from /api/annual-reports) is safe.

Pairs with supabase/migrations/*_documents_filed_at.sql, which adds the
column but deliberately leaves the derivation to ingest.period rather than
re-implementing the fiscal-quarter rule in SQL.
"""

from __future__ import annotations

import argparse

from ingest.config import get_settings
from ingest.db import get_client
from ingest.period import derive_concall_period, filed_at_from_url

# PostgREST caps a single response; documents is small (tens of rows) but
# page anyway so a grown corpus doesn't silently truncate the backfill.
PAGE_SIZE = 500


def _fetch_documents(client) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table("documents")
            .select("id, symbol, doc_type, period, filed_at, source_url")
            .order("id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def plan_updates(rows: list[dict]) -> list[dict]:
    """The `{id, symbol, changes, how}` list for rows whose dating would
    change. Pure — no client — so the decision is testable without a DB."""
    plan: list[dict] = []
    for row in rows:
        changes: dict[str, str] = {}
        how = "given"

        if row["doc_type"] == "concall" and not row.get("period"):
            period, filed_at, how = derive_concall_period(row["source_url"])
            if period:
                changes["period"] = period
            if filed_at and not row.get("filed_at"):
                changes["filed_at"] = filed_at.isoformat()
        elif not row.get("filed_at"):
            # An annual report (or an already-dated concall): its period is
            # authoritative, so only the filing date is filled in.
            filed_at = filed_at_from_url(row["source_url"])
            if filed_at:
                changes["filed_at"] = filed_at.isoformat()

        if changes:
            plan.append({"id": row["id"], "symbol": row["symbol"], "changes": changes, "how": how})
    return plan


def run(dry_run: bool = False) -> dict[str, int]:
    client = get_client(get_settings())
    rows = _fetch_documents(client)
    plan = plan_updates(rows)

    print(f"[backfill] {len(rows)} document(s); {len(plan)} need dating")
    for item in plan:
        described = ", ".join(f"{k}={v}" for k, v in item["changes"].items())
        print(f"[backfill] {item['symbol']}: {described} (via {item['how']})")

    if dry_run:
        print("[backfill] --dry-run: nothing written")
        return {"documents": len(rows), "planned": len(plan), "updated": 0}

    for item in plan:
        client.table("documents").update(item["changes"]).eq("id", item["id"]).execute()
    print(f"[backfill] updated {len(plan)} document(s)")
    return {"documents": len(rows), "planned": len(plan), "updated": len(plan)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing.")
    args = parser.parse_args(argv)
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
