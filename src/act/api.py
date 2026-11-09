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


@app.get("/", response_class=HTMLResponse)
def dashboard():
    result = LATEST or {}
    markets = {m["symbol"].upper(): m for m in result.get("markets", [])}
    cards = "".join(
        f'<article><h2>{symbol}</h2><strong>'
        f'{safe(markets[symbol]["price_usd"]) if symbol in markets else "Unavailable"}'
        '</strong><p>USD</p></article>' for symbol in ("BTC", "ETH", "SOL")
    )
    rows = "".join(
        '<tr>' + ''.join(f'<td>{safe(pool.get(key, "Unavailable"))}</td>' for key in
                        ("name", "pool_address", "price_usd", "liquidity_usd")) + '</tr>'
        for pool in result.get("discoveries", [])[:10]
    ) or '<tr><td colspan="4">No discoveries available; see collector outcomes.</td></tr>'
    account = result.get("paper_account", {})
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autonomous Crypto Trader | Research Status</title><style>
body{{margin:0;background:#0b1220;color:#e2e8f0;font:16px system-ui;line-height:1.6}}
main{{max-width:1100px;margin:auto;padding:32px}}h1{{line-height:1.2}}
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
<div class="cards">{cards}</div><section><h2>Top new Solana pools</h2><div class="scroll">
<table><thead><tr><th>Name</th><th>Pool</th><th>Price USD</th><th>Liquidity USD</th></tr></thead>
<tbody>{rows}</tbody></table></div></section><section><h2>Paper account</h2>
<p>Equity USD: {safe(account.get("equity", "Unavailable"))}</p>
<pre>{safe(json.dumps(account, indent=2))}</pre></section>
<section id="decisions"><h2>Candidate decisions (every candidate)</h2>
<p>SIMULATED - PAPER TRADING ONLY</p>
<pre>{safe(json.dumps(result.get("decision_counts", {}), indent=2))}</pre>
<pre>{safe(json.dumps(result.get("decisions", []), indent=2))}</pre></section>
<section id="positions"><h2>Open paper positions</h2>
<pre>{safe(json.dumps(result.get("paper_positions", []), indent=2))}</pre></section>
<section id="trades"><h2>Paper trade history (including losses)</h2>
<pre>{safe(json.dumps(result.get("paper_trades", []), indent=2))}</pre></section>
<section><h2>Collector outcomes</h2><pre>{safe(json.dumps(result.get("collectors", {}), indent=2))}</pre></section>
<section><h2>Security check outcomes</h2><pre>{safe(json.dumps(result.get("assessments", {}), indent=2))}</pre>
<p>Missing security information is unknown, never safe.</p></section>
<p>Cycles run every 20 minutes while awake. Free hosting may sleep; local logs are ephemeral; configured Supabase storage survives redeploys.
<a href="/health">Health JSON</a> · <a href="/status">Full cycle JSON</a></p></main></body></html>'''
