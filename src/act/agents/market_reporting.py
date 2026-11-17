"""Escaped presentation of recorded Binance analysis, never live prices."""
from html import escape
import json


def scanner_panel(report):
    agents = report.get("agents") or {}
    out = ('<section id="scanner"><h2>// SCANNER · Binance USDT-M · ANALYSIS ONLY</h2>'
           '<p>PAPER MODE · LAST STORED STATE · No order placement. '
           'Scores are uncalibrated evidence, not confidence or entry advice.</p>')
    for key, label in (("scanner", "Market Scanner"), ("technical", "Technical Analysis")):
        result = agents.get(key) or {}
        data = result.get("data") or {}
        status = data.get("status") or result.get("status") or "not_available"
        at = data.get("as_of") or result.get("finished_at") or report.get("finished_at")
        out += '<h3>' + label + '</h3><p>Status: ' + escape(str(status))
        out += ' · As of ' + escape(str(at or "unavailable")) + '</p>'
        if not data.get("candidates"):
            out += '<p>No completed candidates available. Coverage is not implied.</p>'
        # Complete evidence includes n, source URLs, parameters, timestamps and failures.
        recorded = {k: v for k, v in data.items() if k != "state"}
        if result.get("error"):
            recorded["framework_error"] = result["error"]
        recorded["request_provenance"] = result.get("provenance", [])
        out += ('<details><summary>Coverage, candidates, reasons, scores and disagreements</summary>'
                '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%">'
                + escape(json.dumps(recorded, indent=2, allow_nan=False)) + '</pre></details>')
    return out + '</section>'
  
