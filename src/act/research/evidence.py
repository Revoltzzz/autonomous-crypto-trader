"""Conservative research evidence. Never routes decisions into the paper ledger."""
import math
import re
from copy import deepcopy

from act.research.registry import timestamp


def finite(value):
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError("Finite numeric evidence required")
    return float(value)


def metrics(trades, version_id, initial_equity):
    initial_equity = finite(initial_equity)
    if initial_equity <= 0:
        raise ValueError("Positive initial equity required")
    rows = sorted((t for t in trades if t.get("strategy_version_id") == version_id
                   and t.get("record_type") == "forward_paper"),
                  key=lambda t: timestamp(t["closed_at"]))
    if len({t["id"] for t in rows}) != len(rows):
        raise ValueError("Duplicate trade evidence")
    for t in rows:
        if timestamp(t["opened_at"]) > timestamp(t["closed_at"]):
            raise ValueError("Trade chronology invalid")
    pnl = [finite(t["realized_pnl"]) for t in rows]
    gains = sum(p for p in pnl if p > 0)
    losses = -sum(p for p in pnl if p < 0)
    equity = peak = initial_equity
    drawdown = 0.
    for p in pnl:
        equity += p
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    start = min((timestamp(t["opened_at"]) for t in rows), default=None)
    end = max((timestamp(t["closed_at"]) for t in rows), default=None)
    return {"strategy_version_id": version_id, "n": len(rows), "small_n": len(rows) < 100,
            "net_pnl_after_costs": sum(pnl), "expectancy": sum(pnl) / len(rows) if rows else None,
            "profit_factor": gains / losses if losses else None,
            "profit_factor_status": "finite" if losses else "undefined_no_losses",
            "closed_equity_max_dd": drawdown if rows else None,
            "max_dd_scope": "closed trades only; NOT mark-to-market drawdown",
            "data_start": start.isoformat() if start else None,
            "data_end": end.isoformat() if end else None,
            "weeks": (end - start).total_seconds() / 604800 if rows else 0}


def promotion_gates(summary, *, shadow_n=0, backtest_status="insufficient_data",
                    walk_forward_status="insufficient_data", marked_max_dd=None):
    """Evaluator only: no champion mutation/promotion API in this release.

    Require separately measured mark-to-market DD, not understated closed DD.
    None/nonfinite/zero-loss PF is insufficient, never an automatic pass.
    """
    def positive(value):
        return value is not None and math.isfinite(float(value)) and value > 0
    pf = summary.get("profit_factor")
    dd = marked_max_dd
    gates = {
        "trades_ge_100": summary.get("n", 0) >= 100,
        "weeks_ge_8": summary.get("weeks", 0) >= 8,
        "positive_expectancy_after_costs": positive(summary.get("expectancy")),
        "profit_factor_gt_1_3": positive(pf) and pf > 1.3,
        "max_dd_lt_10_percent": dd is not None and math.isfinite(float(dd)) and 0 <= dd < .1,
        "walk_forward_consistency": walk_forward_status == "passed",
        "backtest_available": backtest_status == "completed",
        "forward_shadow_available": shadow_n > 0,
    }
    return {"strategy_version_id": summary["strategy_version_id"], "n": summary.get("n", 0),
            "eligible": all(gates.values()), "promoted": False,
            "gates": gates, "reasons": [k for k, passed in gates.items() if not passed],
            "status": "eligible_review_only" if all(gates.values()) else "insufficient_evidence",
            "metrics": deepcopy(summary)}


def news_context(items, decision_at, terms=(), *, since=None):
    """Relevance = explicit title tokens only, not token identity or sentiment.

    Verification can depend on later fetches; require ALL supporting sources to
    predate the cutoff. Otherwise exclude the item rather than leak its label.
    """
    cutoff = timestamp(decision_at)
    lower = timestamp(since) if since else None
    if lower and lower > cutoff:
        raise ValueError("News window reversed")
    wanted = {str(term).casefold() for term in terms if len(str(term)) >= 3}
    rows = []
    for item in items:
        sources = item.get("sources") or []
        if not sources:
            continue
        times = [timestamp(s["fetched_at"]) for s in sources]
        if any(t > cutoff for t in times) or (lower and not any(t >= lower for t in times)):
            continue
        tokens = set(re.findall(r"[\w]+", item["title"].casefold()))
        if wanted and not wanted.intersection(tokens):
            continue
        label = item.get("verification")
        if label not in ("verified", "unverified"):
            raise ValueError("Unknown news verification label")
        rows.append(deepcopy(item))
    return {"cutoff": decision_at, "since": since, "items": rows, "n": len(rows),
            "status": "available" if rows else "no_matching_predecision_items",
            "coverage": "supplied snapshots only; absence is NOT proof of no news",
            "limit": "Conservative headline corroboration, not fact-checking; no sentiment scores"}


def shadow_record(version_id, market, at, decision, context):
    timestamp(at)
    if context["cutoff"] != at:
        raise ValueError("Shadow context cutoff mismatch")
    return {"record_type": "shadow", "strategy_version_id": version_id, "market": market,
            "at": at, "decision": deepcopy(decision), "news_context": deepcopy(context),
            "ledger_eligible": False}
  
