"""Read-only paper research dashboard."""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from html import escape
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from act.config.settings import Settings
from act.logging_setup import setup_logging
from act.orchestrator.cycle import now, run_cycle

STARTED = time.monotonic()
LATEST = None
LOG = logging.getLogger(__name__)


async def research_loop():
    global LATEST
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
            "persistence": LATEST.get("persistence") if LATEST else None}


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


@app.get("/", response_class=HTMLResponse)
def dashboard():
    result = LATEST or {}
    markets = {m["symbol"].upper(): m for m in result.get("markets", [])}
    cards = "".join(
        f'<article><h2>{symbol}</h2><strong>'
        f'{safe(markets[symbol]["price_usd"]) if symbol in markets else "Unavailable"}'
        '</strong><p>USD</p></article>' for symbol in ("BTC", "ETH", "SOL")
    )
    pools = record_table(result.get("discoveries", [])[:10], [
        ("name", "Name"), ("pool_address", "Pool"), ("price_usd", "Price USD"),
        ("liquidity_usd", "Liquidity USD")], "No discoveries available; see collector outcomes.")
    positions = record_table(result.get("paper_positions", []), [
        ("token", "Token"), ("current_price", "Current quote USD"),
        ("unrealized_pnl", "Net unrealized P&L USD"), ("stop_loss", "Stop USD"),
        ("stop_distance_pct", "Stop distance %"), ("take_profit", "Target USD"),
        ("target_distance_pct", "Target distance %"), ("age_hours", "Age hours"),
        ("stale_quoted", "Stale quote"), ("quote", "Quote provenance")], "No open paper positions.")
    trades = record_table(result.get("paper_trades", []), [
        ("token", "Token"), ("exit_reason", "Exit reason"),
        ("exit_price_basis", "Observed exit quote USD"), ("exit_price", "Slipped exit USD"),
        ("realized_pnl", "Net realized P&L USD"), ("opened_at", "Opened UTC"),
        ("closed_at", "Closed UTC")], "No closed paper trades; no live exit demonstrated.")
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
    account = result.get("paper_account") or {}
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autonomous Crypto Trader | Research Status</title><style>
body{{margin:0;background:#0b1220;color:#e2e8f0;font:16px system-ui;line-height:1.6}}
main{{max-width:1200px;margin:auto;padding:32px}}h1{{line-height:1.2}}
.badge{{color:#fbbf24;font-weight:700}}.cards{{display:flex;gap:16px;flex-wrap:wrap}}
article,section{{background:#152238;border:1px solid #334155;border-radius:12px;padding:20px;margin:16px 0}}
article{{flex:1}}strong{{font-size:24px}}table{{width:100%;border-collapse:collapse}}
td,th{{text-align:left;border-bottom:1px solid #334155;padding:10px;overflow-wrap:anywhere}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.scroll{{overflow:auto}}a{{color:#7dd3fc}}
</style></head><body><main><p class="badge">SIMULATED - PAPER TRADING ONLY</p>
<h1>Autonomous Crypto Trader</h1><p>Live research and risk-gated simulated paper execution. No real trades.</p>
<p>System: {safe(result.get("status", "Waiting for first cycle"))}<br>
Last cycle: {safe(result.get("finished_at", "Not completed yet"))}<br>
Persistence: {safe(result.get("persistence", "Waiting"))}</p>
<div class="cards">{cards}</div><section><h2>Top new Solana pools</h2>{pools}</section>
<section><h2>Paper account</h2><p>Equity USD: {safe(account.get("equity", "Unavailable"))}</p>
<p>Valuation: {safe(result.get("valuation_status", "Unavailable"))}. Missing holding quotes make current equity unavailable, not zero.</p>
<pre>{safe(json.dumps(account, indent=2))}</pre></section>
<section id="positions"><h2>Open paper positions</h2><p class="badge">SIMULATED - PAPER TRADING ONLY</p>
<p>Marks include estimated liquidation fees/slippage. Stale quotes have no mark and cannot trigger exits.</p>{positions}</section>
<section id="trades"><h2>Paper trade history (including losses)</h2>
<p>Net realized P&L includes entry/exit fees and slippage. Exits use the first cycle observing the trigger, not an intracycle fill.</p>{trades}</section>
<section id="management"><h2>Position management cycle</h2>
<pre>{safe(json.dumps(result.get("position_management", {}), indent=2))}</pre>
<pre>{safe(json.dumps(result.get("steps", []), indent=2))}</pre></section>
<section id="decisions"><h2>Candidate decisions (every candidate)</h2>
<h3>Volume profile confirmation</h3>{profiles}
<pre>{safe(json.dumps(result.get("volume_profile_counts", {}), indent=2))}</pre>
<pre>{safe(json.dumps(result.get("decision_counts", {}), indent=2))}</pre>
<pre>{safe(json.dumps(result.get("decisions", []), indent=2))}</pre></section>
<section><h2>Collector outcomes</h2><pre>{safe(json.dumps(result.get("collectors", {}), indent=2))}</pre></section>
<section><h2>Security check outcomes</h2><pre>{safe(json.dumps(result.get("assessments", {}), indent=2))}</pre>
<p>Missing security information is unknown, never safe.</p></section>
<p>Cycles run every 20 minutes while awake. Free hosting may sleep; local logs are ephemeral; configured Supabase storage survives redeploys.
<a href="/health">Health JSON</a> · <a href="/status">Full cycle JSON</a></p></main></body></html>'''
