"""Escaped stored analysis; markets and their coverage are never combined."""
from html import escape
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
