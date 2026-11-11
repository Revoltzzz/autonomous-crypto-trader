"""Offline cycle/executor/atomic persistence regression tests."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from act.config.settings import Settings
from act.domain.models import SecurityAssessment, TokenDiscovery
from act.discovery import Candidate
from act.orchestrator import cycle
from act.paper.ledger import PaperLedger
from act.strategy import StrategySettings, evaluate


@pytest.mark.parametrize("accept", [True, False])
def test_cycle_refreshes_remote_history_and_persists_decisions(tmp_path, monkeypatch, accept):
    # Legacy lifecycle regression; enabled enrichment has dedicated offline tests.
    monkeypatch.setenv("SECURITY_ONCHAIN_ENABLED", "false")
    at = datetime.now(timezone.utc)
    pool = TokenDiscovery(symbol="TEST", mint_address="mint", pool_address="pool",
                          first_seen_at=at - timedelta(hours=1), price_usd=2,
                          liquidity_usd=10000, volume_24h_usd=25000,
                          buys_24h=110, sells_24h=100, source_url="https://example.org")
    security = SecurityAssessment(score=1, lp_locked_pct=80 if accept else None,
                                  mint_authority_revoked=True, freeze_authority_revoked=True,
                                  top_holder_concentration_pct=12, source="offline")
    from act.volume_profile import VolumeProfileFetcher
    async def offline_profile(self, candidate, at=None):
        assert accept, "Hard-filter failures must not request OHLCV"
        return {"status": "available", "val": 1.7, "vah": 1.9, "poc": 1.8,
                "value_area_pct": 70, "candle_count": 12, "timeframe": "5m"}
    monkeypatch.setattr(VolumeProfileFetcher, "profile", offline_profile)
    remote = PaperLedger(tmp_path / "remote.json")
    saved = []
    loads = []

    class Store:
        directory = tmp_path / "reports"

        def load_history(self):
            loads.append(1)
            return remote.history

        def save(self, payload, ledger):
            assert ledger["history"][:len(remote.history)] == remote.history
            assert payload["decisions"][0]["sources"] == ["dexscreener_profiles"]
            assert payload["decisions"][0]["proposal"]["discovery"]["sources"] == ["dexscreener_profiles"]
            assert "rejection_breakdown" in payload and "discovery_stats" in payload
            saved.append((payload, ledger))

    class Collector:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Markets(Collector):
        async def fetch(self):
            remote.account_state({})
            return []

    async def offline_discover(*, options, stats):
        stats["dexscreener_profiles"] = dict(status="success", returned=1, kept_after_cap=1)
        return [Candidate(**pool.model_dump(), sources=["dexscreener_profiles"])]

    class Security(Collector):
        async def fetch(self, mint):
            return security

    monkeypatch.setattr(cycle, "CoinGeckoCollector", Markets)
    monkeypatch.setattr(cycle, "discover", offline_discover)
    monkeypatch.setattr(cycle, "RugCheckCollector", Security)
    settings = Settings(_env_file=None, ledger_path=tmp_path / "local.json",
                        report_dir=tmp_path / "reports")
    result = asyncio.run(cycle.run_cycle(settings, persistence=Store()))
    assert len(loads) == 2 and len(saved) == 1
    assert result["persistence"]["status"] == "persisted"
    assert result["decisions"][0]["decision"] == ("BUY" if accept else "REJECT")
    assert len(saved[0][1]["positions"]) == int(accept)
    assert len(saved[0][0]["decisions"]) == 1
    assert result["decisions"][0]["cycle_id"] == result["cycle_id"]
    if accept:
        assert result["paper_account"]["equity"] < 10000
    else:
        assert "lp_locked_pct" in result["decisions"][0]["reasoning"]

    class FailedStore(Store):
        def save(self, payload, ledger):
            raise RuntimeError("offline persistence refusal")

    with pytest.raises(RuntimeError, match="offline persistence refusal"):
        asyncio.run(cycle.run_cycle(settings, persistence=FailedStore()))
    reports = [json.loads(p.read_text()) for p in settings.report_dir.glob("*.json")]
    assert any(r["status"] == "failed" and r["persistence"]["status"] == "failed"
               for r in reports)
    watch = evaluate(pool, security, cycle_id="offline", account=None)
    assert watch.decision == "REJECT" and watch.proposal is None
    assert "liquidity_usd" in watch.reasoning and "Current equity unavailable" in watch.reasoning


@pytest.mark.parametrize("name", ["min_liquidity_usd", "min_volume_24h_usd",
                                 "min_buys_sells_ratio", "max_pool_age_hours", "target_fraction"])
def test_nonfinite_strategy_settings_rejected(name):
    with pytest.raises(ValidationError):
        StrategySettings(**{name: float("inf")})
