import asyncio
import json

import httpx
import pytest

from act.collectors.base import CollectorError
from act.collectors.coingecko import CoinGeckoCollector
from act.collectors.geckoterminal import GeckoTerminalCollector
from act.collectors.rugcheck import RugCheckCollector
from act.config.settings import Settings
from act.orchestrator.cycle import run_cycle

MINT = "So11111111111111111111111111111111111111112"


def run(collector_class, payload, *args, status=200):
    async def task():
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            async with collector_class(client=client, retries=0) as collector:
                return await collector.fetch(*args)
    return asyncio.run(task())


def test_coingecko_required_metrics():
    rows = run(CoinGeckoCollector, {"solana": {"usd": 150, "usd_market_cap": 100000,
               "usd_24h_vol": 2000, "usd_24h_change": -2}}, ["solana"])
    assert rows[0].symbol == "SOL"
    assert rows[0].price_usd == 150
    assert rows[0].change_24h_pct == -2
    assert rows[0].fetched_at.tzinfo is not None
    assert "coingecko" in rows[0].source


def test_coingecko_missing_metric_fails():
    with pytest.raises(CollectorError, match="missing or invalid"):
        run(CoinGeckoCollector, {"solana": {"usd": 150}}, ["solana"])


def test_rugcheck_partial_object_is_unknown_not_safe():
    result = run(RugCheckCollector, {"score": 12, "risks": []}, MINT)
    assert result.score == 12
    assert result.mint_authority_revoked is None
    assert result.freeze_authority_revoked is None
    assert result.lp_locked_pct is None
    assert "Unavailable: lp_locked_pct" in result.risks


def test_rugcheck_explicit_fields():
    result = run(RugCheckCollector, {"token": {"mintAuthority": None,
                 "freezeAuthority": "active"}, "lpLockedPct": 75,
                 "topHolders": [{"pct": 10}, {"pct": 5}], "score": 25,
                 "risks": [{"name": "Concentration"}]}, MINT)
    assert result.mint_authority_revoked is True
    assert result.freeze_authority_revoked is False
    assert result.lp_locked_pct == 75
    assert result.top_holder_concentration_pct == 15
    assert result.risks == ["Concentration"]


def test_gecko_failure_requires_reverification():
    with pytest.raises(CollectorError, match="endpoint needs re-verification"):
        run(GeckoTerminalCollector, {"error": "not found"}, status=404)


def test_retry_after_is_respected_without_real_sleep():
    async def task():
        delays = []
        calls = 0

        async def sleep(delay):
            delays.append(delay)

        def respond(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "7"})
            return httpx.Response(200, json={"score": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            async with RugCheckCollector(client=client, sleep=sleep) as collector:
                result = await collector.fetch(MINT)
        assert result.score == 1
        assert delays == [7]
        assert calls == 2
    asyncio.run(task())


def test_cycle_records_failure_without_fabrication(tmp_path):
    async def task():
        settings = Settings(report_dir=tmp_path, http_retries=0, _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(500, json={"error": "offline failure"}))) as client:
            with pytest.raises(RuntimeError, match="Cycle failed"):
                await run_cycle(settings, client)
    asyncio.run(task())
    reports = list(tmp_path.glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["status"] == "failed"
    assert report["markets"] == []
    assert report["error"]["type"] == "CollectorError"
