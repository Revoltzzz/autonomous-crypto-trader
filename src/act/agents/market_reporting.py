"""Escaped stored analysis; markets and their coverage are never combined."""
from html import escape
import hashlib
import json


def scanner_panel(report):
    agents = report.get("agents") or {}
    out = ('<section id="scanner"><h2>// SCANNER · ANALYSIS ONLY</h2>'
           '<p>PAPER MODE · LAST STORED STATE · No order placement. '
           'Scores are uncalibrated evidence, not confidence or entry advice.</p>')
    for market in ("spot", "futures"):
        out += '<h3>Binance ' + ("Spot" if market == "spot" else "USDT-M Futures") + '</h3>'
        for key, label in (("scanner_", "Market Scanner"), ("technical_", "Technical Analysis")):
            result = agents.get(key + market) or {}
            data = result.get("data") or {}
            status = data.get("status") or result.get("status") or "not_available"
            at = data.get("as_of") or result.get("finished_at") or report.get("finished_at")
            out += '<h4>' + label + '</h4><p>Status: ' + escape(str(status))
            out += ' · As of ' + escape(str(at or "unavailable")) + '</p>'
            coverage = data.get("coverage")
            if coverage:
                out += '<p>Completed scans this cycle: ' + escape(str(coverage.get("scanned_this_cycle")))
                out += ' / Universe: ' + escape(str(coverage.get("universe_size")))
                out += ' · Cumulative unique: ' + escape(str(coverage.get("cumulative_unique"))) + '</p>'
            if market == "spot" and key == "scanner_":
                out += mover_panel(data)
            if not data.get("candidates"):
                out += '<p>No completed candidates available. Coverage is not implied.</p>'
            for row in data.get("candidates") or []:
                out += '<p><b>' + escape(str(row.get("symbol"))) + '</b> · ' + escape(market)
                if "up_score" in row:
                    out += ' · Up/down score: ' + escape(str(row["up_score"])) + ' / ' + escape(str(row["down_score"]))
                out += '</p>'
            recorded = {k: v for k, v in data.items() if k != "state"}
            if result.get("error"):
                recorded["framework_error"] = result["error"]
            recorded["request_provenance"] = result.get("provenance", [])
            out += ('<details><summary>Coverage, reasons, five-timeframe structure and challenges</summary>'
                    '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%">'
                    + escape(json.dumps(recorded, indent=2, allow_nan=False)) + '</pre></details>')
    return out + '</section>'

def mover_panel(data):
    """Only stored cycle evidence, never a live request or recomputed ranking."""
    snapshot = data.get("mover_snapshot")
    if not snapshot:
        return '<p>Mover ranking unavailable in this stored cycle.</p>'
    try:
        payload = snapshot["payload_json"]
        if hashlib.sha256(payload.encode()).hexdigest() != snapshot["sha256"]:
            raise ValueError("Snapshot checksum mismatch")
        ranking = json.loads(payload)
        summary = {"as_of": ranking["as_of"], "status": ranking["status"],
                   "top_n": ranking["top_n"], "eligible_n": ranking["eligible_n"],
                   "gainers": ranking["gainers"][:ranking["top_n"]],
                   "losers": ranking["losers"][:ranking["top_n"]],
                   "data_gaps": ranking["data_gaps"], "loser_label": ranking["loser_label"]}
        coverage = data.get("coverage") or {}
        summary.update(mover_priority_scanned=coverage.get("mover_priority_scanned", 0),
                       rotation_scanned=coverage.get("rotation_scanned", 0),
                       scanned_selections=coverage.get("selections", []))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        summary = {"status": "MOVER SNAPSHOT UNAVAILABLE / INVALID", "error": str(exc)}
    return ('<h4>Stored mover ranking / selection basis</h4>'
            '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%">'
            + escape(json.dumps(summary, indent=2, allow_nan=False)) + '</pre>')
