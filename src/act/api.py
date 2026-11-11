"""Read-only paper research dashboard. No mutation routes."""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from html import escape
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from act.config.settings import Settings
from act.intelligence import intelligence_panel
from act.logging_setup import setup_logging
from act.orchestrator.cycle import now, run_cycle
from act.persistence import get_persistence
from act.results import load_reports, results_panels

STARTED = time.monotonic()
LATEST = None
RECENT = []
HISTORY_ERROR = None
LOG = logging.getLogger(__name__)


async def research_loop():
    global LATEST, RECENT, HISTORY_ERROR
    settings = Settings()
    while True:
        directory = settings.report_dir / str(uuid4())
        try:
            result = await run_cycle(settings.model_copy(update={"report_dir": directory}))
        except Exception as exc:
            reports = list(directory.glob("*.json"))
            result = json.loads(reports[0].read_text()) if reports else {
                "status": "failed", "finished_at": now(), "steps": [],
                "markets": [], "discoveries": [], "assessments": {},
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        for name, outcome in result.get("collectors", {}).items():
            LOG.info("COLLECTOR %s %s", name, json.dumps(outcome))
        LATEST = result
        try:
            store = get_persistence(settings)
            if hasattr(store, "_request"):
                RECENT = await asyncio.to_thread(load_reports, store)
            else:
                # Local reports live in per-cycle directories. Local-only cache is ephemeral.
                RECENT = ([result] + RECENT)[:30]
            HISTORY_ERROR = None
        except Exception:
            RECENT = []
            HISTORY_ERROR = "Persisted cycle history read failed; no substitute history. See server logs."
            LOG.exception("RESULTS_HISTORY_FAILED")
        try:
            settings.report_dir.mkdir(parents=True, exist_ok=True)
            with (settings.report_dir / "cycles.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, allow_nan=False) + "\n")
        except Exception:
            LOG.exception("Cycle JSONL append failed")
        LOG.info("CYCLE_RESULT %s", json.dumps(result, allow_nan=False))
        await asyncio.sleep(1200)


@asynccontextmanager
async def lifespan(app):
    setup_logging()
    task = asyncio.create_task(research_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Autonomous Crypto Trader", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "awake", "uptime_seconds": round(time.monotonic() - STARTED, 2),
            "paper_only": True,
            "last_cycle_timestamp": LATEST.get("finished_at") if LATEST else None,
            "persistence": LATEST.get("persistence") if LATEST else None,
            "results_history_error": HISTORY_ERROR}


@app.get("/status")
def status():
    return LATEST or {"status": "waiting_for_first_cycle", "paper_only": True}


def safe(value):
    return escape(str(value))


def record_table(records, columns, empty):
    headers = ''.join(f'<th>{safe(label)}</th>' for _, label in columns)
    rows = ''.join('<tr>' + ''.join(
        f'<td>{safe(record.get(key)) if record.get(key) is not None else "Unavailable"}</td>'
        for key, _ in columns) + '</tr>' for record in records)
    if not rows:
        rows = f'<tr><td colspan="{len(columns)}">{safe(empty)}</td></tr>'
    return f'<div class="scroll"><table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table></div>'


def trade_views(trades):
    # Display duration only: prices, fees and P&L are never recomputed.
    rows = []
    for trade in trades:
        row = dict(trade)
        try:
            row["holding_duration"] = str(datetime.fromisoformat(trade["closed_at"])
                                          - datetime.fromisoformat(trade["opened_at"]))
        except (KeyError, TypeError, ValueError):
            row["holding_duration"] = None
        rows.append(row)
    return rows


@app.get("/", response_class=HTMLResponse)
def dashboard():
    result = LATEST or {}
    account = result.get("paper_account") or {}
    profile_rows = []
    for decision in result.get("decisions", []):
        vp = (decision.get("proposal") or {}).get("volume_profile") or {
            "status": "not_attempted", "reason": "Hard filters/equity failed"}
        profile_rows.append(dict(vp, token=decision.get("mint_address"),
                                 decision=decision.get("decision")))
    profiles = record_table(profile_rows, [
        ("token", "Token"), ("decision", "Decision"), ("status", "Volume profile"),
        ("poc", "POC USD"), ("vah", "VAH USD"), ("val", "VAL USD"),
        ("value_area_pct", "Target VA %"), ("actual_value_area_pct", "Actual VA %"),
        ("price_vs_value_area", "Price vs VA"), ("timeframe", "Timeframe"),
        ("candle_count", "Completed candles"), ("reason", "Unavailable reason")],
        "No candidate decisions yet.")
    panels = results_panels(result, RECENT, HISTORY_ERROR) + intelligence_panel(result)
    empty_trades = ("<p>No trades yet - the strategy has not yet produced a BUY.</p>"
                    if account and not result.get("paper_trades") and not result.get("paper_positions") else "")
    positions = record_table(result.get("paper_positions", []), [
        ("token", "Token"), ("entry_price", "Entry USD"), ("current_price", "Current quote USD"),
        ("unrealized_pnl", "Net unrealized P&L USD"), ("stop_loss", "Stop USD"),
        ("stop_distance_pct", "Stop distance %"), ("take_profit", "Target USD"),
        ("target_distance_pct", "Target distance %"), ("age_hours", "Age hours"),
        ("stale_quoted", "Stale quote"), ("quote", "Quote provenance / observed time")],
        "No open paper positions.")
    trades = record_table(trade_views(result.get("paper_trades", [])), [
        ("token", "Token"), ("entry_price", "Slipped entry USD"), ("exit_reason", "Exit reason"),
        ("exit_price_basis", "Observed exit quote USD"), ("exit_price", "Slipped exit USD"),
        ("realized_pnl", "Net realized P&L USD"), ("fees_paid", "Total fees USD"),
        ("opened_at", "Opened UTC"), ("closed_at", "Closed UTC"),
        ("holding_duration", "Holding duration")], "No closed paper trades; no live exit demonstrated.")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autonomous Crypto Trader | Results</title><style>
body{{margin:0;background:#0b1220;color:#e2e8f0;font:16px system-ui;line-height:1.6}}
main{{max-width:1200px;margin:auto;padding:24px}}h1{{line-height:1.2}}
.badge{{color:#fbbf24;font-weight:700}}section{{background:#152238;border:1px solid #334155;border-radius:12px;padding:20px;margin:16px 0}}
table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;border-bottom:1px solid #334155;padding:10px;overflow-wrap:anywhere}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}.scroll{{overflow:auto}}a{{color:#7dd3fc}}
summary{{cursor:pointer;padding:10px 0}}details{{border-bottom:1px solid #334155}}nav{{position:sticky;top:0;background:#0b1220;padding:12px;z-index:1}}
section{{scroll-margin-top:70px}}[role=alert]{{color:#fca5a5}}@media(max-width:600px){{main{{padding:10px}}section{{padding:12px}}}}
</style></head><body><main><p class="badge">SIMULATED - PAPER TRADING ONLY</p>
<h1>Autonomous Crypto Trader</h1><p>Read-only results · no real-money capability · no invented history</p>
<p>System: {safe(result.get("status", "Waiting for first cycle"))}<br>
Last cycle: {safe(result.get("finished_at", "Not completed yet"))}<br>
Persistence: {safe(result.get("persistence", "Waiting"))}</p>
<p>Equity USD: {safe(account.get("equity", "Unavailable"))} · Cash USD: {safe(account.get("cash", "Unavailable"))}</p>
<p>Valuation: {safe(result.get("valuation_status", "Unavailable"))}. Missing holding quotes make equity unavailable, never zero.</p>
{panels}
<section id="positions"><h2>Open paper positions</h2><p>Marks include liquidation fees/slippage. Quote freshness is as of the recorded cycle, not a streaming price. Stale quotes have no mark and cannot trigger exits.</p>{positions}</section>
<section id="trades"><h2>Paper trade history (including losses)</h2>{empty_trades}
<p>Every closed ledger trade is retained, including losses. Net P&L and total fees are ledger values; entry and exit prices include slippage. Historical entry quote and separate slippage dollars were not recorded and are unavailable, not estimated. Exits use cycle-observed quotes, not intracycle fills.</p>{trades}</section>
<section id="decisions"><h2>Volume profile confirmation</h2>{profiles}</section>
<section id="audit"><h2>Latest cycle audit</h2><details><summary>Position management / ordered steps</summary><pre>{safe(json.dumps(result.get("position_management", {}), indent=2))}</pre><pre>{safe(json.dumps(result.get("steps", []), indent=2))}</pre></details>
<details><summary>Markets / pools / assessments / volume profiles / source outcomes</summary><pre>{safe(json.dumps({k: result.get(k) for k in ("markets", "discoveries", "assessments", "volume_profile_counts", "collectors", "discovery_stats", "security_source_stats")}, indent=2))}</pre></details></section>
<p>Cycles run about every 20 minutes after completion while awake. Free hosting may sleep and pauses monitoring. Local-only history is ephemeral; configured Supabase storage survives redeploys. Historical results are not investment advice. <a href="/health">Health JSON</a> · <a href="/status">Full cycle JSON</a></p></main></body></html>'''
