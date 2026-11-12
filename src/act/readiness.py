"""Evidence only. DISPLAY_REFERENCES are NEVER strategy inputs or trading gates.

Pure computations plus read-only record loading and an escaped dashboard view.
No quotes, optimization, extrapolation, backfill or execution capability.
"""
import hashlib
import json
import logging
import math
from collections import Counter
from datetime import datetime, timezone
from statistics import mean, median
from time import perf_counter

VERSION = "readiness-v1"
DISPLAY_MIN_TRADES = 100
DISPLAY_MIN_WEEKS = 8
DISPLAY_MIN_EXPECTANCY = 0
DISPLAY_MIN_PF = 1.3
DISPLAY_MAX_DD_PCT = 10
DISPLAY_REFERENCES = {
    "trades_completed": ">= 100", "weeks_elapsed": ">= 8 (both baselines)",
    "net_expectancy_after_fees": "> 0 USD / closed trade",
    "profit_factor": "> 1.3", "max_drawdown_pct": "< 10%",
    "out_of_sample": "completed reserved-period validation",
    "critical_issues": "0 recorded health flags (not a safety certification)",
}
HEALTH_FIELDS = ("status", "error", "strategy_error", "learning_error", "persistence",
                 "collectors", "position_management")
CYCLE_FIELDS = ("cycle_id", "started_at", "finished_at", "equity_snapshot") + HEALTH_FIELDS
WARNING = ("SMALL-N: descriptive observed samples only; no extrapolation. Render sleep gaps, "
           "missing quotes and preflight selection bias remain. Reference values are NOT enforced.")
LOG = logging.getLogger(__name__)


def date(value):
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.astimezone(timezone.utc) if at.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def metric(value, n, start, caveat, available=True):
    return {"value": value, "n": n, "data_start": start,
            "status": "available" if available else "insufficient_data",
            "caveats": caveat + " " + WARNING}


def project(report):
    return {k: report.get(k) for k in CYCLE_FIELDS}


def load_cycles(store, cutoff):
    """Read compact projections, paginated over ALL durable cycles, not dashboard's 30.

    10k safety cap fails loudly rather than misreporting a truncated lifetime window.
    No readiness snapshots are written into old cycles. Local history scope is explicit.
    """
    if hasattr(store, "_request"):
        result = []
        for offset in range(0, 10001, 100):
            rows = store._request("GET", "cycles", params={
                "select": ",".join("report->" + k for k in CYCLE_FIELDS),
                "order": "completed_at.asc,id.asc", "completed_at": "lt." + cutoff,
                "limit": "100", "offset": str(offset)}).json()
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise ValueError("Invalid readiness history response")
            result.extend(rows)
            if len(rows) < 100:
                if len(result) > 10000:
                    break
                return result
        raise ValueError("Readiness history safety cap reached; no truncated metrics")
    paths = sorted(store.directory.parent.glob("*/*.json"))
    reports = [json.loads(p.read_text()) for p in paths]
    return [project(r) for r in reports if r.get("cycle_id") and date(r.get("finished_at"))
            and date(r["finished_at"]) < date(cutoff)]


def health_flags(report):
    """Conservative recorded-health proxy, not an issue tracker or security audit."""
    flags = []
    if report.get("status") not in (None, "success"):
        flags.append("status")
    for k in ("error", "strategy_error", "learning_error"):
        if report.get(k):
            flags.append(k)
    persistence = report.get("persistence") or {}
    if persistence.get("status") == "failed" or persistence.get("error"):
        flags.append("persistence.status/error")
    for name, outcome in (report.get("collectors") or {}).items():
        if outcome.get("status") in ("failed", "degraded") or outcome.get("error"):
            flags.append("collectors." + name + ".status/error")
    if (report.get("position_management") or {}).get("stale_tokens"):
        flags.append("position_management.stale_tokens")
    return flags


def compute(report, cycles, computed_at):
    """Replay with the same report, projected stored cycles and computed_at.

    Current report is a pending input, committed atomically with this snapshot.
    Net realized_pnl ALREADY includes modeled fees and slipped prices: never subtract twice.
    """
    at = date(computed_at)
    if at is None:
        raise ValueError("Timezone-aware computed_at required")
    cycles = sorted({r["cycle_id"]: project(r) for r in cycles
                     if r.get("cycle_id") != report.get("cycle_id")}.values(),
                    key=lambda r: (r.get("finished_at") or "", r["cycle_id"]))
    current = project(report)
    # A replay sees the post-write persistence field; normalize pending atomic input.
    current["persistence"] = {"status": "pending_atomic_commit"}
    inputs = cycles + [current]
    trades_known = "paper_trades" in report and "paper_account" in report and not report.get("strategy_error")
    trades = report.get("paper_trades") or []
    positions = report.get("paper_positions") or []
    n = len(trades)
    trade_dates = [date(t.get("opened_at")) for t in trades + positions]
    valid_dates = [d for d in trade_dates if d and d <= at]
    first_trade = min(valid_dates).isoformat() if valid_dates else None
    closes = [date(t.get("closed_at")) for t in trades]
    close_start = min(d for d in closes if d).isoformat() if any(closes) else None
    persisted_dates = [date(r.get("started_at")) for r in cycles]
    persisted_dates = [d for d in persisted_dates if d and d <= at]
    first_cycle = min(persisted_dates).isoformat() if persisted_dates else None
    metrics = {"trades_completed": metric(n if trades_known else None, n, close_start,
        "Full current closed ledger, not recent-cycle sample. Zero is an observed count only when ledger available.", trades_known)}
    for key, start in (("weeks_since_first_persisted_cycle", first_cycle),
                       ("weeks_since_first_trade", first_trade)):
        metrics[key] = metric((at - date(start)).total_seconds() / 604800 if start else None,
                             len(cycles) if key.endswith("cycle") else len(valid_dates), start,
                             "Wall-clock elapsed, NOT continuous uptime; sleep gaps not interpolated.", bool(start))
    pnl = [t.get("realized_pnl") for t in trades]
    pnl_valid = trades_known and n > 0 and all(number(v) for v in pnl)
    costs_valid = pnl_valid and all(number(t.get("fees_paid")) and t["fees_paid"] >= 0
                                   and number(t.get("entry_price")) and number(t.get("exit_price")) for t in trades)
    base = "Recorded realized_pnl includes fees and embedded modeled slippage; separate historic slippage dollars not recorded."
    metrics["win_rate_pct"] = metric(100 * sum(v > 0 for v in pnl) / n if pnl_valid else None,
                                    n, close_start, "Break-even is not a win.", pnl_valid)
    metrics["net_expectancy_after_fees"] = metric(mean(pnl) if costs_valid else None,
        n, close_start, base + ("" if costs_valid else " No closed trades or cost/P&L data not recorded."), costs_valid)
    wins = sum(v for v in pnl if number(v) and v > 0)
    losses = -sum(v for v in pnl if number(v) and v < 0)
    metrics["profit_factor"] = metric(wins / losses if costs_valid and losses > 0 else None,
        n, close_start, "Gross winning net-P&L sum / absolute losing net-P&L sum; no additional cost subtraction. "
        + ("Zero gross losses: undefined, never infinity." if losses == 0 else "") + base,
        costs_valid and losses > 0)
    snapshots = [(r.get("cycle_id"), r.get("equity_snapshot")) for r in inputs]
    valid = [(cid, s) for cid, s in snapshots if isinstance(s, dict) and date(s.get("timestamp"))
             and date(s["timestamp"]) <= at and number(s.get("equity")) and s["equity"] > 0]
    valid.sort(key=lambda row: date(row[1]["timestamp"]))
    peak, dd = None, 0.0
    for _, s in valid:
        peak = max(peak or s["equity"], s["equity"])
        dd = max(dd, (peak - s["equity"]) / peak * 100)
    metrics["max_drawdown_pct"] = metric(dd if len(valid) >= 2 else None, len(valid),
        valid[0][1]["timestamp"] if valid else None,
        "Observed equity snapshots ONLY; not intracycle/lifetime drawdown before capture began. "
        + str(len(snapshots) - len(valid)) + " missing/invalid snapshots; gaps not interpolated. "
        + "Window end: " + (valid[-1][1]["timestamp"] if valid else "unavailable"), len(valid) >= 2)
    durations = [(end - date(t.get("opened_at"))).total_seconds() / 3600
                 for t, end in zip(trades, closes) if end and date(t.get("opened_at"))
                 and end >= date(t["opened_at"]) and end <= at]
    metrics["duration_hours"] = metric({"min": min(durations), "mean": mean(durations),
        "median": median(durations), "max": max(durations)} if durations else None,
        len(durations), close_start, "Closed-trade timestamps only; missing durations: " + str(n - len(durations)), bool(durations))
    reasons = Counter(t.get("exit_reason") or "data not recorded" for t in trades)
    metrics["exit_reasons"] = metric(dict(reasons) if n else None, n, close_start,
        "Recorded labels: stop_loss, take_profit, max_age; unknown labels preserved.", n > 0)
    flags = [{"cycle_id": r.get("cycle_id"), "fields": health_flags(r)} for r in inputs]
    affected = [r for r in flags if r["fields"]]
    health_known = bool(cycles) and all(r.get("status") in ("success", "failed") for r in inputs)
    metrics["critical_issues"] = metric(len(affected) if health_known else None, len(inputs),
        first_cycle, "Cumulative flagged cycles in retained window. Fields: status!=success; error; strategy_error; learning_error; "
        "persistence.status=failed/error; collectors.*.status=failed/degraded/error; position_management.stale_tokens. "
        "Historical flags do not prove unresolved bugs. No resolution registry, manual audit, missing-cycle, "
        "readiness-write or unrecorded issue coverage; current atomic persistence cannot certify its own success.", health_known)
    gates = {}
    for key, threshold in DISPLAY_REFERENCES.items():
        if key == "out_of_sample":
            item = metric(None, 0, None, "Plan documented, awaiting data. No validation run or profitability evidence.", False)
            passed = False
        elif key == "weeks_elapsed":
            a, b = metrics["weeks_since_first_persisted_cycle"], metrics["weeks_since_first_trade"]
            known = a["value"] is not None and b["value"] is not None
            item = metric({"persisted_cycle": a["value"], "first_trade": b["value"]},
                          b["n"], {"persisted_cycle": first_cycle, "first_trade": first_trade},
                          "Both baselines must reach 8 weeks. Wall clock is not uptime.", known)
            passed = known and min(a["value"], b["value"]) >= DISPLAY_MIN_WEEKS
        else:
            item = dict(metrics[key])
            v = item["value"]
            passed = v is not None and {"trades_completed": lambda: v >= DISPLAY_MIN_TRADES,
                "net_expectancy_after_fees": lambda: v > DISPLAY_MIN_EXPECTANCY, "profit_factor": lambda: v > DISPLAY_MIN_PF,
                "max_drawdown_pct": lambda: v < DISPLAY_MAX_DD_PCT, "critical_issues": lambda: v == 0}[key]()
            # A single closed trade cannot establish distributional aggregates.
            if key in ("net_expectancy_after_fees", "profit_factor") and n < 2:
                item["status"] = "insufficient_data"
                item["caveats"] += " Fewer than 2 closed trades; value descriptive only."
        item["threshold"] = threshold
        item["status"] = "insufficient_data" if item["status"] == "insufficient_data" else "pass" if passed else "fail"
        gates[key] = item
    return {"version": VERSION, "computed_at": computed_at, "display_only": True,
        "gates": gates, "metrics": metrics, "health_flags": affected,
        "open_context": {"n": len(positions) if trades_known else None, "positions": positions,
                         "caveat": "Open unrealized P&L is NOT expectancy or realized profit factor."},
        "provenance": {"current_cycle_id": report.get("cycle_id"),
            "current_input_status": "pending same atomic cycle persistence",
            "persisted_cycle_ids": [r["cycle_id"] for r in cycles],
            "cycles_sha256": digest(inputs), "closed_trade_ids": [t.get("id") for t in trades],
            "trades_sha256": digest(trades), "positions_sha256": digest(positions),
            "cycle_fields": list(CYCLE_FIELDS), "history_cutoff_exclusive": report.get("started_at"),
            "window": "all retained projected cycles + current cycle; full closed ledger",
            "replay": "compute(current_report, projected_persisted_cycles, computed_at); SHA256 canonical sorted JSON"}}


def capture_readiness(report, store):
    started = perf_counter()
    try:
        if store is None:
            raise ValueError("Readiness persistence store unavailable")
        cycles = load_cycles(store, report["started_at"])
        report["readiness"] = compute(report, cycles, report["finished_at"])
        report["readiness"]["provenance"]["storage_scope"] = (
            "Supabase durable cycles" if hasattr(store, "_request") else "Local sibling cycle directories; ephemeral on Render")
        report["readiness_elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        LOG.info("READINESS_STATS %s", json.dumps({"n_cycles": len(cycles), "elapsed_ms": report["readiness_elapsed_ms"],
                 "gates": {k: v["status"] for k, v in report["readiness"]["gates"].items()}}))
    except Exception as exc:
        report.pop("readiness", None)
        report["readiness_error"] = str(exc)
        LOG.exception("READINESS_FAILED; no substituted metrics; strategy unchanged")


def readiness_panel(report, reports):
    from act.results import raw, table, text

    out = '<section id="readiness"><h2>Strategy validation / readiness (evidence only)</h2>'
    out += '<p>DISPLAY ONLY — not permission for real money. No strategy gates or enforcement.</p>'
    snapshot = report.get("readiness")
    if not snapshot:
        return out + '<p role="alert">Readiness unavailable / not recorded: ' + text(report.get("readiness_error")) + '</p></section>'
    gates = snapshot["gates"]
    passing = sum(g["status"] == "pass" for g in gates.values())
    insufficient = sum(g["status"] == "insufficient_data" for g in gates.values())
    out += '<p>' + str(passing) + ' of 7 gates passing; ' + str(insufficient) + ' insufficient data. Computed ' + text(snapshot["computed_at"]) + '</p>'
    columns = [("gate", "Gate"), ("value", "Current value"), ("threshold", "Reference only"),
               ("status", "Status"), ("n", "n"), ("data_start", "Data start"), ("caveats", "Coverage / small-n warnings")]
    out += table([dict(g, gate=k) for k, g in gates.items()], columns, "No recorded gates")
    out += '<details><summary>Supporting metrics (small-n, costs, durations, exit reasons)</summary>'
    out += table([dict(m, gate=k) for k, m in snapshot["metrics"].items()],
                 [c for c in columns if c[0] != "threshold"], "No metrics") + '</details>'
    out += '<details><summary>Open position context (not closed performance)</summary>' + raw(snapshot["open_context"]) + '</details>'
    out += '<details><summary>Replay provenance and recorded health flags</summary>' + raw(snapshot["provenance"]) + raw(snapshot["health_flags"]) + '</details>'
    history = [{"at": r["readiness"]["computed_at"], "passing": sum(g["status"] == "pass" for g in r["readiness"]["gates"].values())}
               for r in reports[:30] if r.get("readiness")]
    out += '<details><summary>Readiness history (latest 30 cycles; raw retained, no backfill)</summary>'
    return out + table(history, [("at", "Computed UTC"), ("passing", "Passing / 7")], "No earlier readiness snapshot") + '</details></section>'
