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


def now():
    return datetime.now(timezone.utc).isoformat()


async def run_cycle(settings=None, client=None) -> dict:
    settings = settings or Settings()
    if settings.paper_only is not True:
        raise ValueError("Only paper research is permitted")
    log = get_logger(__name__)
    report = {"cycle_id": str(uuid4()), "started_at": now(), "status": "running",
              "steps": [], "markets": [], "discoveries": [], "assessments": {}}
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_dir / (report["cycle_id"] + ".json")
    report["report_path"] = str(path)

    def step(name):
        report["steps"].append({"step": name, "at": now()})
        log.info("Cycle %s: %s", report["cycle_id"], name)

    failure = None
    try:
        options = {"client": client, "timeout": settings.http_timeout,
                   "retries": settings.http_retries}
        step("fetch CoinGecko markets")
        async with CoinGeckoCollector(**options) as collector:
            report["markets"] = [m.model_dump(mode="json") for m in await collector.fetch()]
        step("fetch GeckoTerminal Solana pools (unverified endpoint)")
        async with GeckoTerminalCollector(**options) as collector:
            pools = await collector.fetch()
            report["discoveries"] = [p.model_dump(mode="json") for p in pools]
        step("fetch RugCheck security reports")
        async with RugCheckCollector(**options) as collector:
            for pool in pools:
                assessment = await collector.fetch(pool.mint_address)
                report["assessments"][pool.mint_address] = assessment.model_dump(mode="json")
        report["status"] = "success"
        step("research complete; no trades executed")
    except Exception as exc:
        failure = exc
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        log.exception("Research cycle failed; no substitute data used")
    finally:
        report["finished_at"] = now()
        with path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
    if failure is not None:
        raise RuntimeError(f"Cycle failed: {failure}; report: {path}") from failure
    return report


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_cycle())
