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


def test_gecko_http_failure_preserves_status():
    with pytest.raises(CollectorError, match="GeckoTerminal HTTP 404"):
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


# Captured 2026-11-09 from the public v2 endpoint with include=base_token.
# One real pool and its included token, retaining string metrics/null values.
# https://api.geckoterminal.com/api/v2/networks/solana/new_pools?include=base_token
@pytest.fixture
def gecko_payload():
    return {
        "data": [{
            "id": "solana_CwmnwFwZwRgyRgmW6R1tywFiiHrP4kfb8k21Qbiv86SW",
            "type": "pool",
            "attributes": {
                "address": "CwmnwFwZwRgyRgmW6R1tywFiiHrP4kfb8k21Qbiv86SW",
                "name": "BTF / SOL",
                "pool_created_at": "2026-11-09T11:48:57Z",
                "base_token_price_usd": "0.00000320908072449485400259412428003833972186331400282738465999256198",
                "base_token_price_native_currency": "0.000000037299034770659",
                "quote_token_price_usd": "86.152962777101881022624195182890343489780632498",
                "quote_token_price_native_currency": "1.0",
                "base_token_price_quote_token": "0.00000003729903477",
                "quote_token_price_base_token": "26810345.2582275",
                "fdv_usd": "3209.080724",
                "market_cap_usd": None,
                "reserve_in_usd": "2486.45901713773",
                "volume_usd": {period: "616.8381931653" for period in
                               ("m5", "m15", "m30", "h1", "h6", "h24")},
                "price_change_percentage": {period: "22.207" for period in
                                            ("m5", "m15", "m30", "h1", "h6", "h24")},
                "transactions": {period: {"buys": 10, "sells": 2, "buyers": 10, "sellers": 2}
                                 for period in ("m5", "m15", "m30", "h1", "h6", "h24")},
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_B8b6nPeSmPb9w93vBeoWYxjJHA8y1HVdJd45Rppepump", "type": "token"}},
                "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112", "type": "token"}},
                "dex": {"data": {"id": "pump-fun", "type": "dex"}},
            },
        }],
        "included": [{
            "id": "solana_B8b6nPeSmPb9w93vBeoWYxjJHA8y1HVdJd45Rppepump",
            "type": "token",
            "attributes": {
                "address": "B8b6nPeSmPb9w93vBeoWYxjJHA8y1HVdJd45Rppepump",
                "name": "Bag Traded Funds", "symbol": "BTF", "decimals": 6,
                "image_url": None, "coingecko_coin_id": None,
            },
        }],
    }


def test_gecko_real_v2_contract(gecko_payload):
    rows = run(GeckoTerminalCollector, gecko_payload)
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Bag Traded Funds"
    assert row.symbol == "BTF"
    assert row.mint_address == "B8b6nPeSmPb9w93vBeoWYxjJHA8y1HVdJd45Rppepump"
    assert row.pool_address == "CwmnwFwZwRgyRgmW6R1tywFiiHrP4kfb8k21Qbiv86SW"
    assert row.first_seen_at.isoformat() == "2026-11-09T11:48:57+00:00"
    assert row.price_usd == pytest.approx(0.000003209080724494854)
    assert row.liquidity_usd == pytest.approx(2486.45901713773)
    assert row.volume_24h_usd == pytest.approx(616.8381931653)
    assert (row.buys_24h, row.sells_24h) == (10, 2)
    assert row.source_url == f"https://www.geckoterminal.com/solana/pools/{row.pool_address}"
    assert row.unverified_fields == []


def test_gecko_unknown_metadata_and_null_liquidity(gecko_payload):
    del gecko_payload["included"]
    gecko_payload["data"][0]["attributes"]["reserve_in_usd"] = None
    row = run(GeckoTerminalCollector, gecko_payload)[0]
    assert row.name is None and row.symbol is None and row.liquidity_usd is None
    assert row.unverified_fields == ["name", "symbol", "liquidity_usd"]
    assert row.buys_24h == 10


@pytest.mark.parametrize("payload", [{}, {"data": {}}, {"data": []}, {"data": [None]}, []])
def test_gecko_unexpected_shape_fails_loudly(payload):
    with pytest.raises(CollectorError, match="expected v2 pool data"):
        run(GeckoTerminalCollector, payload)


def test_gecko_invalid_relationship_fails_loudly(gecko_payload):
    gecko_payload["data"][0]["relationships"]["base_token"]["data"]["id"] = "eth_address"
    with pytest.raises(CollectorError, match="Expected solana_<mint>"):
        run(GeckoTerminalCollector, gecko_payload)


def test_gecko_invalid_optional_shape_is_not_silently_empty(gecko_payload):
    gecko_payload["data"][0]["attributes"]["transactions"] = []
    with pytest.raises(CollectorError, match="Expected object.*transactions.*list"):
        run(GeckoTerminalCollector, gecko_payload)


@pytest.mark.parametrize("retry_after, expected_delay", [(None, 30), ("7", 30), ("65", 65)])
def test_gecko_rate_limit_pacing_and_request_headers(gecko_payload, retry_after, expected_delay):
    async def task():
        delays, requests = [], []

        async def sleep(delay):
            delays.append(delay)

        def respond(request):
            requests.append(request)
            assert dict(request.url.params) == {"include": "base_token"}
            assert request.headers["accept"] == "application/json"
            assert "AutonomousCryptoTrader" in request.headers["user-agent"]
            if len(requests) == 1:
                headers = {"Retry-After": retry_after} if retry_after is not None else {}
                return httpx.Response(429, headers=headers)
            return httpx.Response(200, json=gecko_payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            original_headers = dict(client.headers)
            async with GeckoTerminalCollector(client=client, retries=1, sleep=sleep) as collector:
                rows = await collector.fetch()
            assert dict(client.headers) == original_headers
        assert len(rows) == 1 and len(requests) == 2
        assert delays == [expected_delay]
    asyncio.run(task())


def test_gecko_rate_limit_exhaustion_reports_actual_status():
    with pytest.raises(CollectorError, match="GeckoTerminal HTTP 429.*retries exhausted"):
        run(GeckoTerminalCollector, {}, status=429)
