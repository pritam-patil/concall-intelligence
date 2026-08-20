#!/usr/bin/env python3
"""Vector-only vs. hybrid (RRF) retrieval, side by side, on a fixed set of
sample queries — real embeddings, real RPC calls, real results, no mocking.

    uv run python scripts/compare_hybrid.py
    uv run python scripts/compare_hybrid.py --top-k 5 --fusion-weight 0.5

Calls match_chunks_filtered (vector-only) and match_chunks_hybrid (RRF —
both in supabase/migrations/) with the SAME query embedding and top_k, for
each query in QUERIES below, against whatever Supabase project SUPABASE_URL
(ingest/.env or the environment) points at. Two qualitative queries and
three numbers-heavy ones on purpose — hybrid is specifically expected to
help the latter (see match_chunks_hybrid's migration comment for why: exact
figures and proper nouns don't embed distinctively, but they're exactly
what full-text search is good at), and the qualitative queries are there to
check hybrid does NOT regress where vector search was already doing well.

DIGIT-HIT COUNT: a rough, cheap, defensible proxy for "did this actually
help the numbers-heavy queries" beyond eyeballing content previews — how
many of the top-K results contain a digit at all. Not a claim about
correctness (a chunk with a digit in it isn't necessarily the RIGHT digit)
— just a quick, real signal that's cheaper to compute than an LLM-graded
relevance judgment and doesn't require one. Read the actual content
previews for the real read on quality; this is a supporting data point, not
the finding by itself.

Depends only on things already in ingest's dependency set (postgrest,
ingest.config, ingest.providers.embeddings) — safe to run against either
the real hosted project or a local-supabase-stack stand-in without any
extra setup beyond the usual env vars.
"""

from __future__ import annotations

import argparse
import re

from postgrest import SyncPostgrestClient

from ingest.config import get_settings
from ingest.providers.embeddings import get_embeddings_provider

QUERIES = [
    # Qualitative — vector search's home turf. Included so the comparison
    # shows hybrid holding steady here, not just winning on the numeric ones.
    "management commentary on margins",
    "risks related to global economic conditions",
    # Numbers-heavy — exact figures/line items a bge embedding tends to
    # blur into "some financial number", but a literal keyword match finds
    # directly.
    "dividend per share",
    "earnings per share and net profit",
    "capital expenditure guidance for next fiscal year",
]

DIGIT_RE = re.compile(r"\d")


def _client(settings):
    """Same /rest/v1-prefix workaround the local-supabase-stack skill
    documents for Python verification scripts — supabase-py's
    create_client() assumes Kong-gateway routing a bare PostgREST instance
    (or this project's rest_proxy.py stand-in) may or may not have,
    depending on how SUPABASE_URL is set for this run. A raw
    SyncPostgrestClient pointed at SUPABASE_URL + "/rest/v1" works against
    both a real hosted project and a bare PostgREST alike."""
    return SyncPostgrestClient(
        f"{settings.supabase_url}/rest/v1",
        headers={
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
        },
    )


def run_query(client, provider, query: str, top_k: int, fusion_weight: float) -> dict:
    [vector] = provider.embed([query])

    vector_only = (
        client.rpc(
            "match_chunks_filtered",
            {
                "query_embedding": vector,
                "match_count": top_k,
                "filter_symbol": None,
                "filter_doc_type": None,
                "filter_period": None,
            },
        )
        .execute()
        .data
    )
    hybrid = (
        client.rpc(
            "match_chunks_hybrid",
            {
                "query_embedding": vector,
                "query_text": query,
                "match_count": top_k,
                "fusion_weight": fusion_weight,
                "filter_symbol": None,
                "filter_doc_type": None,
                "filter_period": None,
            },
        )
        .execute()
        .data
    )
    return {"query": query, "vector_only": vector_only, "hybrid": hybrid}


def digit_hits(results: list[dict]) -> int:
    return sum(1 for r in results if DIGIT_RE.search(r["content"]))


def overlap_count(a: list[dict], b: list[dict]) -> int:
    a_ids = {(r["symbol"], r["page"], r["content"][:60]) for r in a}
    return sum(1 for r in b if (r["symbol"], r["page"], r["content"][:60]) in a_ids)


def print_side(label: str, results: list[dict]) -> None:
    print(f"  {label}:")
    if not results:
        print("    (no results)")
        return
    for i, r in enumerate(results, 1):
        extra = ""
        if "vector_rank" in r:
            extra = f" [v_rank={r['vector_rank']}, t_rank={r['text_rank']}]"
        preview = r["content"][:140].replace("\n", " ")
        print(
            f"    [{i}] {r['symbol']} {r['doc_type']} p.{r['page']} "
            f"score={r['score']:.4f}{extra} — {preview!r}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top-k", type=int, default=None, help="default: HYBRID_TOP_K env / 10")
    parser.add_argument(
        "--fusion-weight", type=float, default=None, help="default: HYBRID_FUSION_WEIGHT env / 0.5"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    top_k = args.top_k or settings.hybrid_top_k
    fusion_weight = args.fusion_weight if args.fusion_weight is not None else settings.hybrid_fusion_weight

    client = _client(settings)
    provider = get_embeddings_provider(settings)

    print(f"top_k={top_k} fusion_weight={fusion_weight} provider={settings.embeddings_provider}\n")

    for query in QUERIES:
        result = run_query(client, provider, query, top_k, fusion_weight)
        v, h = result["vector_only"], result["hybrid"]
        print(f"=== {query!r} ===")
        print_side("vector-only (match_chunks_filtered)", v)
        print_side("hybrid (match_chunks_hybrid, RRF)", h)
        print(
            f"  digit-hit count: vector-only {digit_hits(v)}/{len(v)}, "
            f"hybrid {digit_hits(h)}/{len(h)}"
        )
        print(f"  result overlap (same chunk in both top-{top_k}): {overlap_count(v, h)}/{top_k}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
