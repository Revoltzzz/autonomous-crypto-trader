"""Read-only results views; snapshots copy ledger-derived account values, never simulate."""
import json
import math
from datetime import datetime
from html import escape

from act.risk.limits import RiskLimits
from act.security import ConcentrationSettings
from act.strategy import StrategySettings


LIMIT = 30


def capture_results(report):
    """Presentation metadata in existing cycle JSON, saved by the existing atomic RPC."""
    account = report.get("paper_account") or {}
    report["equity_snapshot"] = {
        "timestamp": report.get("finished_at"),
        "equity": account.get("equity"), "cash": account.get("cash"),
        "open_positions_count": len(report["paper_positions"]) if "equity" in account else None,
        "valuation_status": report.get("valuation_status", "unavailable"),
    }
    report["results_config"] = {
        **{"STRATEGY_" + k.upper(): v for k, v in StrategySettings().model_dump().items()},
        **{k.upper(): v for k, v in ConcentrationSettings().model_dump().items()
           if k.startswith("security_") or k in ("solana_rpc_cycle_budget", "solana_rpc_retries")},
        **{k.upper(): v for k, v in vars(RiskLimits()).items()},
    }


def load_reports(store):
    """Bounded, newest-first durable reports. Called once after each cycle, not per request."""
    if hasattr(store, "_request"):
        rows = store._request("GET", "cycles", params={
            "select": "report", "order": "completed_at.desc", "limit": str(LIMIT)}).json()
        if not isinstance(rows, list) or any(not isinstance(r.get("report"), dict) for r in rows):
            raise ValueError("Invalid persisted results history")
        return [r["report"] for r in rows]
    paths = sorted(store.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [json.loads(p.read_text()) for p in paths[:LIMIT]]


def text(value):
    if value is None:
        return "Unavailable"
    return escape(str(value))


def raw(value):
    return '<pre>' + text(json.dumps(value, indent=2, allow_nan=False)) + '</pre>'


def table(rows, columns, empty):
    head = ''.join('<th>' + text(label) + '</th>' for _, label in columns)
    body = ''.join('<tr>' + ''.join('<td>' + text(r.get(k)) + '</td>' for k, _ in columns)
                   + '</tr>' for r in rows)
    return ('<div class="scroll"><table><thead><tr>' + head + '</tr></thead><tbody>'
            + (body or '<tr><td colspan="' + str(len(columns)) + '">' + text(empty) + '</td></tr>')
            + '</tbody></table></div>')


def equity_chart(reports):
    points = [r.get("equity_snapshot") for r in reversed(reports)]
    points = [p for p in points if p and p.get("timestamp")]
    valid = [p["equity"] for p in points if isinstance(p.get("equity"), (int, float))
             and math.isfinite(p["equity"])]
    if not valid:
        return '<p>No equity snapshots available. No fabricated history.</p>'
    low, high = min(valid), max(valid)
    span = high - low or max(abs(high) * .02, 1)
    segments, segment, circles = [], [], []
    times = [datetime.fromisoformat(p["timestamp"]).timestamp() for p in points]
    start, end = min(times), max(times)
    for p, at in zip(points, times):
        value = p.get("equity")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            if segment:
                segments.append(segment)
            segment = []
            continue
        x = 50 + 700 * (at - start) / (end - start) if end != start else 400
        y = 125 - (value - (high + low) / 2) / span * 140
        segment.append(f'{x:.2f},{y:.2f}')
        circles.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#38bdf8">'
                       f'<title>{text(p["timestamp"])}: USD {text(value)}</title></circle>')
    if segment:
        segments.append(segment)
    lines = ''.join('<polyline points="' + ' '.join(s) + '" fill="none" stroke="#38bdf8" '
                    'stroke-width="3"/>' for s in segments)
    return (f'<p>Observed equity range USD {text(low)} – {text(high)}. '
            f'{len(valid)} valued snapshots; {len(points) - len(valid)} unavailable (gaps).</p>'
            '<svg viewBox="0 0 800 250" role="img" aria-label="Paper equity over time" '
            'style="width:100%;max-height:280px;background:#0b1220">'
            '<path d="M50 30V210H750" fill="none" stroke="#64748b"/>' + lines
            + ''.join(circles) + f'<text x="55" y="35" fill="#e2e8f0">USD {text(high)}</text>'
            + '</svg>' + f'<p>Window: {text(points[0]["timestamp"])} → {text(points[-1]["timestamp"])} UTC.</p>'
            '<p>Snapshots begin with this dashboard release; no synthetic backfill. '
            'Only the latest 30 cycles are displayed; earlier persisted snapshots are retained. '
            'One observation is a point, not an invented line. Sleep creates observation gaps.</p>')


def decision_panel(reports):
    items = [(r, d) for r in reports for d in reversed(r.get("decisions", []))][:200]
    output = ['<section id="decision-log"><h2>Decision log</h2><p>Latest 200 candidates across '
              'the loaded 30-cycle window. Every reason is preserved; expand for raw security provenance.</p>']
    for report, d in items:
        proposal = d.get("proposal") or {}
        evidence = proposal.get("security_evidence") or report.get("security_evidence", {}).get(d.get("mint_address"), {})
        output.append('<details><summary>' + text(d.get("created_at", report.get("finished_at")))
                      + ' · ' + text(d.get("symbol") or d.get("mint_address")) + ' · '
                      + text(d.get("decision")) + '</summary>'
                      + table([d], [("mint_address", "Token"), ("pool_address", "Pool"),
                                    ("decision", "Verdict"), ("reasoning", "All reasoning"),
                                    ("failure_reasons", "Failures"), ("missing_fields", "Missing")], "")
                      + '<h3>Security fields: source + raw values</h3>' + raw(evidence)
                      + '<h3>Persisted proposal / discovery / volume profile</h3>' + raw(proposal) + '</details>')
    if not items:
        output.append('<p>No persisted candidate decisions available.</p>')
    return ''.join(output) + '</section>'


def trend_rows(reports):
    return [{"timestamp": r.get("finished_at"), "status": r.get("status"),
             "discovery": r.get("discovery_stats"), "rejections": r.get("rejection_breakdown"),
             "security": r.get("security_source_stats")} for r in reversed(reports)]


def trend_panel(reports):
    output = ['<section id="cycle-trends"><h2>Cycle / rejection trends</h2><p>Latest 30 persisted cycles, '
              'oldest first. Rejection labels overlap; missing_security_data and failed_security_check '
              'are separate. Unrecorded counts are unavailable, not zero.</p>']
    if not reports:
        output.append('<p>No persisted cycle history available.</p>')
    for row in trend_rows(reports):
        output.append('<details><summary>' + text(row["timestamp"]) + ' · ' + text(row["status"]) + '</summary>'
                      + '<h3>Discovery: seen / kept after dedup</h3>' + raw(row["discovery"])
                      + '<h3>Security coverage per field / source</h3>' + raw(row["security"]) + '</details>')
        breakdown = row["rejections"] or {}
        numeric = {k: v for k, v in breakdown.items() if type(v) in (int, float) and v >= 0}
        numeric.update({"reason: " + k: v for k, v in (breakdown.get("by_reason") or {}).items()
                        if type(v) in (int, float) and v >= 0})
        maximum = max(numeric.values(), default=1) or 1
        output.append('<p>' + text(row["timestamp"]) + '</p>')
        for label, count in numeric.items():
            output.append('<div>' + text(label) + ': ' + text(count)
                          + f'<div style="background:#38bdf8;height:8px;width:{100 * count / maximum:.2f}%"></div></div>')
        output.append("<details><summary>All rejection labels and combinations</summary>"
                      + raw(row["rejections"]) + "</details>")
    return ''.join(output) + '</section>'


def results_panels(latest, reports, error=None):
    account = latest.get("paper_account") or {}
    cfg = latest.get("results_config") or {}
    positions = latest.get("paper_positions")
    stats = latest.get("security_source_stats") or {}
    risk = {"open_positions": len(positions) if positions is not None else None,
            "position_cap": cfg.get("STRATEGY_MAX_OPEN_POSITIONS"),
            "open_risk_usd": account.get("open_risk_usd"), "equity": account.get("equity"),
            "cash": account.get("cash"), "risk_per_trade_pct": cfg.get("MAX_RISK_PER_TRADE_PCT"),
            "stop_fraction": cfg.get("STRATEGY_STOP_FRACTION"),
            "target_fraction": cfg.get("STRATEGY_TARGET_FRACTION"),
            "rpc_calls": stats.get("rpc_calls"), "rpc_budget": stats.get("budget")}
    warning = '<p role="alert">Results history unavailable: ' + text(error) + '</p>' if error else ''
    return ('<nav><a href="#equity">Equity</a> · <a href="#positions">Positions</a> · '
            '<a href="#trades">Trades</a> · <a href="#decision-log">Decisions</a> · '
            '<a href="#risk">Risk</a> · <a href="#cycle-trends">Trends</a></nav>' + warning
            + '<section id="equity"><h2>Paper equity curve</h2>' + equity_chart(reports)
            + '<details><summary>Exact persisted equity / cash snapshots</summary>'
            + table([r["equity_snapshot"] for r in reversed(reports) if r.get("equity_snapshot")],
                    [("timestamp", "UTC"), ("equity", "Equity USD"), ("cash", "Cash USD"),
                     ("open_positions_count", "Open positions"), ("valuation_status", "Valuation")],
                    "No snapshots yet") + '</details></section>'
            + '<section id="risk"><h2>Risk &amp; cycle health</h2>' + raw(risk)
            + '<details><summary>Effective STRATEGY_* / SECURITY_* configuration and risk caps</summary>'
            + (raw(cfg) if cfg else '<p>Configuration not recorded for this cycle.</p>') + '</details>'
            + '<p>Last cycle: ' + text(latest.get("finished_at")) + ' · Status: ' + text(latest.get("status"))
            + '</p><p>429/retry counts: unavailable unless explicitly present in persisted source stats; '
            'errors below are not retry counters.</p><details><summary>Source health / errors / recorded RPC stats</summary>'
            + raw(latest.get("collectors")) + raw(stats) + raw(latest.get("discovery_stats"))
            + '</details></section>' + decision_panel(reports) + trend_panel(reports))
