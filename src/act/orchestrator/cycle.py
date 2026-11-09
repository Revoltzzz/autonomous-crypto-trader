"""Research cycles with evidence-gated paper execution only."""
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from act.collectors.coingecko import CoinGeckoCollector
from act.collectors.geckoterminal import GeckoTerminalCollector
from act.collectors.rugcheck import RugCheckCollector
from act.config.settings import Settings
from act.logging_setup import get_logger, setup_logging
from act.persistence import get_persistence, ledger_snapshot, restore_ledger
from act.strategy import run_strategy


def now():
    return datetime.now(timezone.utc).isoformat()


async def run_cycle(settings=None, client=None, persistence=None) -> dict:
    settings = settings or Settings()
    if settings.paper_only is not True:
        raise ValueError("Only paper research is permitted")
    log = get_logger(__name__)
    report = {"cycle_id": str(uuid4()), "started_at": now(), "status": "running",
              "paper_only": True, "steps": [], "markets": [], "discoveries": [],
              "decisions": [], "paper_positions": [], "paper_trades": [],
              "assessments": {}, "collectors": {name: {"status": "skipped"}
              for name in ("CoinGecko", "GeckoTerminal", "RugCheck")}}
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_dir / (report["cycle_id"] + ".json")
    report["report_path"] = str(path)

    def step(name):
        report["steps"].append({"step": name, "at": now()})
        log.info("Cycle %s: %s", report["cycle_id"], name)

    failure = None
    store = None
    ledger = None
    active = None
    pools = []
    try:
        store = persistence or get_persistence(settings)
        ledger = await asyncio.to_thread(restore_ledger, store, settings)
        options = {"client": client, "timeout": settings.http_timeout,
                   "retries": settings.http_retries}
        active = "CoinGecko"
        step("fetch CoinGecko markets")
        async with CoinGeckoCollector(**options) as collector:
            report["markets"] = [m.model_dump(mode="json") for m in await collector.fetch()]
        report["collectors"][active] = {"status": "success"}
        active = "GeckoTerminal"
        step("fetch verified GeckoTerminal /api/v2/networks/solana/new_pools; 429 Retry-After/backoff handling")
        async with GeckoTerminalCollector(**options) as collector:
            pools = await collector.fetch()
            report["discoveries"] = [p.model_dump(mode="json") for p in pools]
        report["collectors"][active] = {"status": "success"}
        active = "RugCheck"
        step("fetch RugCheck security reports")
        async with RugCheckCollector(**options) as collector:
            for pool in pools:
                assessment = await collector.fetch(pool.mint_address)
                report["assessments"][pool.mint_address] = assessment.model_dump(mode="json")
        report["collectors"][active] = {"status": "success"}
        active = None
        report["status"] = "success"
    except Exception as exc:
        failure = exc
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if active:
            report["collectors"][active] = {"status": "failed", "error": report["error"]}
        log.exception("Research cycle failed; no substitute data used")
    finally:
        snapshot = None
        if ledger is not None:
            try:
                step("evaluate every candidate; SIMULATED PAPER TRADING ONLY")
                # Failed collection never opens a trade; missing assessments fail closed.
                assessments = report["assessments"] if failure is None else {}
                report["decisions"], prices = run_strategy(
                    pools, assessments, ledger, settings, report["cycle_id"])
                for decision in report["decisions"]:
                    log.info("PAPER_DECISION %s", json.dumps(decision, allow_nan=False))
                report["decision_counts"] = {verdict: sum(d["decision"] == verdict
                    for d in report["decisions"]) for verdict in ("BUY", "REJECT", "WATCH")}
                report["paper_positions"] = [p.model_dump(mode="json") for p in ledger.positions.values()]
                report["paper_trades"] = [t.model_dump(mode="json") for t in ledger.closed_trades]
                missing = sorted({p.token for p in ledger.positions.values()} - prices.keys())
                if missing:
                    # Save history and position effects without inventing a current equity.
                    report["paper_account"] = {"error": "Missing current marks", "mints": missing}
                    report["valuation_status"] = "unavailable"
                    snapshot = ledger_snapshot(ledger, report["paper_account"])
                else:
                    account = ledger.account_state(prices).model_dump(mode="json")
                    report["paper_account"] = account
                    snapshot = ledger_snapshot(ledger, account)
                step("paper strategy complete: " + json.dumps(report["decision_counts"]))
            except Exception as exc:
                failure = failure or exc
                report["status"] = "failed"
                report["strategy_error"] = str(exc)
                log.exception("Paper strategy failed; no silent substitute state")
        report["finished_at"] = now()
        report["persistence"] = {"backend": type(store).__name__, "status": "pending"}
        with path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
        try:
            if store is None:
                raise RuntimeError("Persistence initialization failed")
            report["persistence"]["status"] = "persisted"
            await asyncio.to_thread(store.save, report, snapshot)
            log.info("PERSISTED_CYCLE %s backend=%s", report["cycle_id"], type(store).__name__)
        except Exception as exc:
            report["persistence"].update(status="failed", error=str(exc))
            failure = failure or exc
            log.exception("Cycle persistence failed; no silent local fallback")
        path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    if failure is not None:
        raise RuntimeError(f"Cycle failed: {failure}; report: {path}") from failure
    return report


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_cycle())
