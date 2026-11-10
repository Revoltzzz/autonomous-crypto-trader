"""Research cycles with evidence-gated paper execution only."""
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from act.collectors.coingecko import CoinGeckoCollector
from act.collectors.geckoterminal import GeckoTerminalCollector as GeckoTerminalCollector
from act.collectors.rugcheck import RugCheckCollector
from act.config.settings import Settings
from act.discovery import discover, rejection_breakdown
from act.logging_setup import get_logger, setup_logging
from act.paper.positions import manage_positions, position_views
from act.persistence import get_persistence, ledger_snapshot, restore_ledger
from act.strategy import collect_volume_profiles, run_strategy
from act.security import enrich_security
from act.results import capture_results


def now():
    return datetime.now(timezone.utc).isoformat()


async def run_cycle(settings=None, client=None, persistence=None) -> dict:
    settings = settings or Settings()
    if settings.paper_only is not True:
        raise ValueError("Only paper research is permitted")
    log = get_logger(__name__)
    report = {"cycle_id": str(uuid4()), "started_at": now(), "status": "running",
              "paper_only": True, "steps": [], "markets": [], "discoveries": [],
              "discovery_stats": {}, "rejection_breakdown": {},
              "security_evidence": {}, "security_source_stats": {},
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
        try:
            async with CoinGeckoCollector(**options) as collector:
                report["markets"] = [m.model_dump(mode="json") for m in await collector.fetch()]
            report["collectors"][active] = {"status": "success"}
        except Exception as exc:
            report["collectors"][active] = {"status": "failed", "error": str(exc)}
            log.exception("Market context unavailable; continuing independent pool discovery")
        active = "Discovery"
        step("fetch ordered keyless discovery sources; bounded Retry-After-aware retries")
        pools = await discover(options=options, stats=report["discovery_stats"])
        report["discoveries"] = [p.model_dump(mode="json") for p in pools]
        report["collectors"][active] = {"status": "success", "candidates": len(pools)}
        gt = report["discovery_stats"].get("geckoterminal_new", {})
        report["collectors"]["GeckoTerminal"] = {"status": gt.get("status", "skipped")}
        active = "RugCheck"
        step("fetch RugCheck security reports")
        async with RugCheckCollector(**options) as collector:
            for pool in pools:
                assessment = await collector.fetch(pool.mint_address)
                report["assessments"][pool.mint_address] = assessment.model_dump(mode="json")
        report["collectors"][active] = {"status": "success"}
        active = "SolanaRPC"
        step("security: bounded onchain primary verification after unchanged market preflight")
        report["assessments"], report["security_evidence"], report["security_source_stats"] = await enrich_security(
            pools, report["assessments"], client=client)
        security_stats = report["security_source_stats"]
        report["collectors"][active] = {"status": "degraded" if security_stats.get("error_candidates") else
            "success" if security_stats.get("enabled") else "skipped", **security_stats}
        log.info("SECURITY_SOURCES %s", json.dumps(security_stats, allow_nan=False))
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
                ledger = await asyncio.to_thread(restore_ledger, store, settings)
                step(f"position management: refresh quotes -> mark -> exits ({len(ledger.positions)} open positions)")
                management = await manage_positions(ledger, settings, client=client)
                report["position_management"] = management
                step("evaluate every candidate; SIMULATED PAPER TRADING ONLY")
                assessments = report["assessments"] if failure is None else {}
                profile_at = datetime.now(timezone.utc)
                step("volume profiles: hard-filter preflight -> cached OHLCV confirmation")
                profiles = await collect_volume_profiles(
                    pools, assessments, ledger, settings, client=client, at=profile_at,
                    position_prices=management["prices"])
                report["volume_profile_counts"] = {
                    "available": sum(p["status"] == "available" for p in profiles.values()),
                    "unavailable": sum(p["status"] == "unavailable" for p in profiles.values()),
                    "attempted_pools": len(profiles),
                    "not_attempted_candidates": sum(p.pool_address not in profiles for p in pools),
                }
                report["volume_profiles"] = profiles
                log.info("VOLUME_PROFILE %s", json.dumps(report["volume_profile_counts"]))
                report["decisions"], prices = run_strategy(
                    pools, assessments, ledger, settings, report["cycle_id"],
                    position_prices=management["prices"], at=profile_at, volume_profiles=profiles)
                provenance = {p.mint_address: p for p in pools}
                for decision in report["decisions"]:
                    p = provenance[decision["mint_address"]]
                    decision["sources"] = getattr(p, "sources", ["geckoterminal_new"])
                    decision["pool_address"] = p.pool_address
                    decision["proposal"] = dict(decision.get("proposal") or {},
                        security_evidence=report["security_evidence"].get(p.mint_address, {}),
                        discovery={"sources": decision["sources"], "pool_address": p.pool_address,
                                   "source_url": p.source_url,
                                   "alternate_pool_addresses": getattr(p, "alternate_pool_addresses", [])},
                        failure_reasons=decision.get("failure_reasons", []),
                        missing_fields=decision.get("missing_fields", []))
                    log.info("PAPER_DECISION %s", json.dumps(decision, allow_nan=False))
                report["rejection_breakdown"] = rejection_breakdown(report["decisions"])
                log.info("REJECTION_BREAKDOWN %s", json.dumps(report["rejection_breakdown"]))
                report["decision_counts"] = {verdict: sum(d["decision"] == verdict
                    for d in report["decisions"]) for verdict in ("BUY", "REJECT", "WATCH")}
                observations = {p["id"]: p["quote"] for p in management["observations"]}
                report["paper_positions"] = position_views(ledger, prices, observations)
                report["paper_trades"] = [t.model_dump(mode="json") for t in ledger.closed_trades]
                missing = sorted({p.token for p in ledger.positions.values()} - prices.keys())
                if missing:
                    report["paper_account"] = {"error": "Missing current marks", "mints": missing}
                    report["valuation_status"] = "unavailable"
                    snapshot = ledger_snapshot(ledger, report["paper_account"])
                else:
                    account = ledger.account_state(prices).model_dump(mode="json")
                    report["paper_account"] = account
                    report["valuation_status"] = "fresh_cycle_quotes"
                    snapshot = ledger_snapshot(ledger, account)
                step("paper strategy complete: " + json.dumps(report["decision_counts"]))
            except Exception as exc:
                failure = failure or exc
                report["status"] = "failed"
                report["strategy_error"] = str(exc)
                log.exception("Paper strategy failed; no silent substitute state")
        report["finished_at"] = now()
        capture_results(report)
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
            report["status"] = "failed"
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
