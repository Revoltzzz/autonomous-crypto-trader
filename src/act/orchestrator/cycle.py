"""One honest research cycle. No automatic execution, even in paper mode."""
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


def now():
    return datetime.now(timezone.utc).isoformat()


async def run_cycle(settings=None, client=None, persistence=None) -> dict:
    settings = settings or Settings()
    if settings.paper_only is not True:
        raise ValueError("Only paper research is permitted")
    log = get_logger(__name__)
    report = {"cycle_id": str(uuid4()), "started_at": now(), "status": "running",
              "paper_only": True, "steps": [], "markets": [], "discoveries": [],
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
        step("fetch GeckoTerminal Solana pools (unverified endpoint)")
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
        step("research complete; no trades executed")
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
                prices = {m["symbol"]: m["price_usd"] for m in report["markets"]}
                account = ledger.account_state(prices).model_dump(mode="json")
                report["paper_account"] = account
                snapshot = ledger_snapshot(ledger, account)
            except Exception as exc:
                report["paper_account"] = {"error": str(exc)}
                # Preserve remote state rather than replacing it with incomplete data.
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
