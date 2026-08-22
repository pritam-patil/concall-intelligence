#!/usr/bin/env python3
"""Smoke eval for the retrieval + cited-Q&A stack.

Eleven hand-written questions, each with the filing it should be answered
from (or, for one, the expectation that the system REFUSES). For each:

  - RETRIEVAL: POST /api/search {mode:"ask", top_k:5} and record hit@5 —
    is the expected company in the top 5? mode "ask" is EXACTLY the
    retrieval /api/ask feeds the model (web/src/lib/retrieval.ts: hybrid
    vector + keyword-shaped full-text, RRF-fused) and also returns
    `max_score`, the top-1 cosine similarity the confidence gate reads —
    which is what the threshold sweep below uses.
  - GENERATION: POST /api/ask, consume the NDJSON stream, and check that
    the answer carries numbered [n] citations (the format the prompt asks
    for — n indexes the `sources` event, 1-based, same as the UI's
    Markdown renderer) that point at REAL retrieved passages — not invented
    ones (the failure mode that actually erodes trust). "Grounded" here =
    every cited n is within 1..len(sources), AND at least one citation
    lands in the expected company's document.

Black-box: talks only to a running dev server (stdlib urllib, no deps).
Requires that server up with a POPULATED database — see eval/README.md.
The ground-truth questions are grounded in the REAL seed concalls (verified
against actual chunk content: INFY attrition 13%, TMPV dividend Rs 3/share,
etc.), not guessed.

    python3 eval/smoke.py
    ASK_API_URL=... SEARCH_API_URL=... python3 eval/smoke.py
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

SEARCH_URL = os.environ.get("SEARCH_API_URL", "http://localhost:3000/api/search")
ASK_URL = os.environ.get("ASK_API_URL", "http://localhost:3000/api/ask")

# top_k passed to /api/ask per request. The refusal THRESHOLD is server-side
# (ASK_SIMILARITY_THRESHOLD) — this eval reports each question's top-1 score
# so a good threshold can be chosen offline (see the sweep at the end),
# then set in the server env.
ASK_TOP_K = int(os.environ.get("EVAL_ASK_TOP_K", "8"))

# Ground truth. expect="REFUSE" means: no relevant source exists, the system
# should say "not found in the covered filings" and cite nothing.
QUESTIONS = [
    {"q": "What did Infosys management say about attrition during the quarter?", "expect": "INFY"},
    {"q": "What dividend per share did Tata Motors Passenger Vehicles announce?", "expect": "TMPV"},
    {"q": "What did HDFC Bank management say about deposit growth and competition?", "expect": "HDFCBANK"},
    {"q": "What did TCS say about wage revisions or salary hikes?", "expect": "TCS"},
    {"q": "What did Reliance management say about margins and the demand environment?", "expect": "RELIANCE"},
    {"q": "What did Tata Motors' commercial vehicle business say about demand and diesel costs?", "expect": "TMCV"},
    {"q": "What is Infosys's revenue growth guidance for the year?", "expect": "INFY"},
    {"q": "What did HDFC Bank say about credit or loan growth?", "expect": "HDFCBANK"},
    {"q": "What did TCS management say about AI and clients' technology decisions?", "expect": "TCS"},
    # Regression: vector-only retrieval buried RIL's capex passages (annual
    # report p.44, concall p.26) under boilerplate and the model refused; the
    # keyword channel is what surfaces them. Also the landing page's example.
    {"q": "What did Reliance say about its capex plans and spending?", "expect": "RELIANCE"},
    {"q": "What did management say about their cryptocurrency or bitcoin strategy?", "expect": "REFUSE"},
]

# Numbered citation markers: [3] or [3, 5] — mirrors web/src/app/components/
# Markdown.tsx's INLINE pattern, so the eval reads exactly what the UI links.
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def cited_indexes(answer: str) -> list[int]:
    return [int(n) for group in CITATION_RE.findall(answer) for n in group.split(",")]

# The exact phrase both refusal gates emit. A refusal can come from the
# code-path confidence gate (the `refused` flag on the done event) OR from
# the LLM grounding gate (the model writes this phrase itself, flag still
# false) — treat either as a refusal, or a correct refusal reads as a FAIL.
REFUSAL = "not found in the covered filings"

# /api/ask is scoped by the expected company for answerable questions: each
# question names its company unambiguously, and a real UI would pass that as
# a filter. Without it, "what did <company> say about <topic>" retrieves a
# cross-company mix dominated by whoever discusses <topic> most, crowding the
# named company out (measured — see ingest/NOTES.md). hit@5 below stays
# UNFILTERED, so it still measures whether raw retrieval finds the right
# company.
SCOPE_ASK_BY_SYMBOL = os.environ.get("EVAL_SCOPE_ASK", "1") != "0"


def post_json(url: str, body: dict) -> urllib.request.addinfourl:
    return urllib.request.urlopen(
        urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=120,
    )


def retrieve_top5(query: str) -> tuple[list[dict], float]:
    """(top-5 chunks in the order /api/ask would see them, top-1 cosine score)."""
    resp = json.loads(post_json(SEARCH_URL, {"query": query, "mode": "ask", "top_k": 5}).read())
    return resp["results"], float(resp["max_score"])


def ask(question: str, symbol: str | None = None) -> dict:
    """Consume the NDJSON stream into {sources, answer, refused, error}."""
    body = {"question": question, "top_k": ASK_TOP_K}
    if symbol:
        body["symbol"] = symbol
    resp = post_json(ASK_URL, body)
    sources, answer, refused, error = [], "", None, None
    buffer = ""
    for raw in resp:
        buffer += raw.decode()
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            evt = json.loads(line)
            if evt["type"] == "sources":
                sources = evt["sources"]
            elif evt["type"] == "delta":
                answer += evt["text"]
            elif evt["type"] == "done":
                refused = evt["refused"]
            elif evt["type"] == "error":
                error = evt["error"]
    return {"sources": sources, "answer": answer, "refused": refused, "error": error}


def evaluate() -> list[dict]:
    rows = []
    for i, item in enumerate(QUESTIONS, 1):
        query, expect = item["q"], item["expect"]
        top5, top1_score = retrieve_top5(query)
        top5_symbols = [r["symbol"] for r in top5]

        if expect == "REFUSE":
            hit5 = None
            rank = None
        else:
            hit5 = expect in top5_symbols
            rank = top5_symbols.index(expect) + 1 if hit5 else None

        scope = expect if (SCOPE_ASK_BY_SYMBOL and expect != "REFUSE") else None
        result = ask(query, symbol=scope)
        citations = cited_indexes(result["answer"])
        has_citation = len(citations) > 0
        # Refusal from EITHER gate: the done-event flag (code-path threshold)
        # or the phrase in the answer text (LLM grounding gate).
        refused = bool(result["refused"]) or (REFUSAL in result["answer"].lower())

        # A citation is "grounded" iff its number indexes a chunk the ask
        # endpoint actually retrieved — i.e. the model didn't invent one.
        sources = result["sources"]
        cited_set = set(citations)
        grounded = bool(cited_set) and all(1 <= n <= len(sources) for n in cited_set)

        # Does at least one citation land in the EXPECTED company's document?
        cited_expected = any(
            1 <= n <= len(sources) and sources[n - 1]["symbol"] == expect for n in cited_set
        )

        # Verdict categories: PASS (answered + grounded citation in the right
        # doc), REFUSED (safely declined — retrieval didn't surface the fact;
        # unhelpful but NOT a hallucination), FAIL (answered but citation
        # missing / invented / wrong doc), ERROR (generation errored, e.g.
        # the free-tier daily quota).
        if result["error"]:
            verdict = "ERROR"
        elif expect == "REFUSE":
            verdict = "PASS" if (refused and not has_citation) else "FAIL"
        elif refused:
            verdict = "REFUSED"
        else:
            verdict = "PASS" if (has_citation and grounded and cited_expected) else "FAIL"

        rows.append({
            "i": i, "expect": expect, "q": query, "top5_symbols": top5_symbols,
            "hit5": hit5, "rank": rank, "top1_score": top1_score,
            "refused": refused, "has_citation": has_citation,
            "grounded": grounded, "cited_expected": cited_expected,
            "error": result["error"], "verdict": verdict,
            "answer": result["answer"], "n_sources": len(result["sources"]),
        })
    return rows


def fmt(v) -> str:
    if v is True:
        return "yes"
    if v is False:
        return "no"
    if v is None:
        return "-"
    return str(v)


def print_table(rows: list[dict]) -> None:
    header = ["#", "expect", "hit@5", "rank", "top1", "refused", "cites", "grnd", "exp_doc", "verdict"]
    widths = [2, 8, 5, 4, 5, 7, 5, 4, 7, 7]
    print("\n" + "  ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        cells = [
            str(r["i"]), r["expect"], fmt(r["hit5"]), fmt(r["rank"]),
            f"{r['top1_score']:.3f}", fmt(r["refused"]), fmt(r["has_citation"]),
            fmt(r["grounded"]), fmt(r["cited_expected"]), r["verdict"],
        ]
        print("  ".join(c.ljust(w) for c, w in zip(cells, widths)))

    answerable = [r for r in rows if r["expect"] != "REFUSE"]
    hits = sum(1 for r in answerable if r["hit5"])
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("PASS", "REFUSED", "FAIL", "ERROR")}
    print(f"\nhit@5: {hits}/{len(answerable)} answerable questions")
    print(
        f"verdict: {counts['PASS']} PASS, {counts['REFUSED']} REFUSED (safe), "
        f"{counts['FAIL']} FAIL, {counts['ERROR']} ERROR"
    )


def print_threshold_sweep(rows: list[dict]) -> None:
    """Offline: for candidate ASK_SIMILARITY_THRESHOLD values, how many
    answerable questions would be WRONGLY refused (top1 below threshold) and
    is the REFUSE question correctly below? The gating score is the top-1
    cosine similarity /api/ask's gate reads (mode "ask" returns it as
    max_score), so this predicts server refusal behavior without restarting
    per threshold."""
    answerable = [r for r in rows if r["expect"] != "REFUSE"]
    refuse = [r for r in rows if r["expect"] == "REFUSE"]
    ans_scores = sorted(r["top1_score"] for r in answerable)
    print("\n--- threshold sweep (top-1 vector score is what the gate sees) ---")
    print(f"answerable top-1 scores: min={ans_scores[0]:.3f} max={ans_scores[-1]:.3f}")
    for r in refuse:
        print(f"REFUSE question top-1 score: {r['top1_score']:.3f}")
    print(f"{'threshold':>9}  {'false-refusals':>14}  {'refuse-caught-by-gate':>21}")
    for thr in [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        false_ref = sum(1 for r in answerable if r["top1_score"] < thr)
        refuse_caught = all(r["top1_score"] < thr for r in refuse)
        print(f"{thr:>9.2f}  {false_ref:>14}  {fmt(refuse_caught):>21}")


def main() -> int:
    rows = evaluate()
    print_table(rows)
    print_threshold_sweep(rows)
    # Show the refusal question's answer and one answerable answer for eyeballing.
    for r in rows:
        if r["expect"] == "REFUSE" or r["i"] == 1:
            print(f"\n[{r['i']}] {r['q']}\n  answer: {r['answer'][:400]}")
    # Non-zero only on HARD failures (hallucinated/missing citations, errors).
    # REFUSED is a safe outcome (declined rather than fabricated) and doesn't
    # fail the smoke gate, though it's surfaced above as a recall flag.
    hard = sum(1 for r in rows if r["verdict"] in ("FAIL", "ERROR"))
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
