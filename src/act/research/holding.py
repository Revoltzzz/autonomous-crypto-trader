"""Post-close news evidence from already recorded cycle snapshots only.

No new feeds, sentiment, imputed returns, or edits to old trades/checkpoints.
"""
import json
from copy import deepcopy

from act.research.evidence import news_context
from act.research.registry import encoded, timestamp

REPORT_LIMIT = 500
ITEM_LIMIT = 2000


def recorded_news(store):
    if hasattr(store, "_request"):
        rows = store._request("GET", "cycles", params={
            "select": "report->research_news", "order": "completed_at.desc",
            "limit": str(REPORT_LIMIT), "report->research_news": "not.is.null"}).json()
        if not isinstance(rows, list) or len(rows) > REPORT_LIMIT:
            raise ValueError("Invalid recorded news response")
        return [row["research_news"] for row in rows]
    paths = sorted(store.directory.glob("*.json"),
                   key=lambda p: p.stat().st_mtime_ns, reverse=True)[:REPORT_LIMIT]
    return [json.loads(p.read_text(encoding="utf-8")).get("research_news") for p in paths]


def holding_news(report, store):
    if report.get("paper_only") is not True:
        raise ValueError("PAPER ONLY required")
    trades = report.get("paper_trades") or []
    if not trades:
        return {"status": "no_closed_trades", "records": [], "n": 0}
    snapshots = recorded_news(store) + [report.get("research_news")]
    items = {}
    observed_snapshots = 0
    for snapshot in snapshots:
        if not snapshot or not snapshot.get("cycle_news"):
            continue
        observed_snapshots += 1
        for item in snapshot["cycle_news"].get("items", []):
            items.setdefault(encoded(item), deepcopy(item))
    truncated = len(items) > ITEM_LIMIT
    # Latest recorded source fetch first, not headline publication date.
    ordered = sorted(items.values(), key=lambda item: max(
        timestamp(s["fetched_at"]) for s in item["sources"]), reverse=True)[:ITEM_LIMIT]
    records = []
    for trade in trades:
        start, end = trade.get("opened_at"), trade.get("closed_at")
        if not start or not end:
            records.append({"trade_id": trade["id"], "status": "insufficient_data",
                            "reason": "Missing observed holding timestamps"})
            continue
        terms = (trade.get("token") or "", trade.get("symbol") or "")
        records.append({"trade_id": trade["id"], "market": trade.get("market", "solana"),
            "opened_at": start, "closed_at": end, "record_type": "holding_period_news",
            "ledger_eligible": False,
            "relevant": news_context(ordered, end, terms, since=start),
            "market_context": news_context(ordered, end, since=start),
            "status": "partial_snapshot_coverage"})
    return {"status": "partial_snapshot_coverage", "records": records, "n": len(records),
            "snapshots_n": observed_snapshots, "items_n": len(ordered),
            "item_cap_reached": truncated, "report_limit": REPORT_LIMIT,
            "coverage": "Bounded stored snapshots only; absence is not proof of no news; "
                        "all source fetches strictly before close and at least one during holding",
            "limits": "No sentiment/causality or returns inferred. Title-token relevance only. "
                      "Late corroboration excluded. Older/truncated history may be missing."}
