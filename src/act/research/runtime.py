"""Additive per-cycle news recording, after all existing paper decisions.

Deliberately does not claim historical strategy attribution or full news archive
coverage. Earlier cycles' reports remain authoritative immutable snapshots.
"""
from act.research.evidence import news_context


def capture_news_evidence(report):
    if report.get("paper_only") is not True:
        raise ValueError("PAPER ONLY required")
    news = ((report.get("agents") or {}).get("news") or {})
    data = news.get("data") or {}
    items = data.get("items") or []
    records = []
    for d in report.get("decisions", []):
        at = d.get("created_at")
        if not at:
            records.append({"decision_id": d.get("id"), "market": "solana",
                            "status": "missing_decision_timestamp"})
            continue
        terms = (d.get("symbol") or "", d.get("mint_address") or "")
        records.append({"decision_id": d.get("id"), "market": "solana",
            "strategy_version_id": None, "attribution_status": "not_activated",
            "news_context": news_context(items, at, terms)})
    for i, d in enumerate((report.get("binance_paper") or {}).get("decisions", [])):
        at = d.get("at")
        if not at:
            records.append({"decision_index": i, "market": d.get("market"),
                            "status": "missing_decision_timestamp"})
            continue
        # Exact symbol title tokens only; ticker/base disambiguation not inferred.
        records.append({"decision_index": i, "market": d.get("market"),
            "strategy_version_id": None, "attribution_status": "not_activated",
            "news_context": news_context(items, at, (d.get("symbol") or "",))})
    at = data.get("as_of")
    return {"paper_only": True, "status": "recorded" if at else "news_unavailable",
            "news_agent_status": news.get("status", "missing"), "as_of": at,
            "decision_records": records,
            "cycle_news": news_context(items, at) if at else None,
            "limitations": ["Current news snapshot only, not a complete historical news archive",
                "Solana decisions usually predate this cycle's news fetch; no lookahead backfill",
                "No strategy activation or historical trade attribution inferred",
                "No sentiment scores; corroboration is not fact-checking",
                "Post-trade holding-period attribution and per-version evidence accumulation deferred"]}
  
