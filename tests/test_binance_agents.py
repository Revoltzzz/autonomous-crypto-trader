"""Synthetic offline fixtures only; no market observations or live requests."""
import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest

from act.agents import Context, Registry, freeze, run_agents
from act.agents.market_reporting import scanner_panel
from act.agents.scanner import (MarketScannerAgent, TechnicalAnalysisAgent, TIMEFRAMES,
                                candles, challenges, coverage_state, directional_score,
                                indicators, liquidity, restored_state, structure, universe)
from act.binance_data import BinanceData, GeoBlocked, Malformed, NetworkError, RateLimited
from act.persistence import LocalPersistence


def bars(direction=1):
    rows = []
    for i in range(60):
        close = 100 + direction * i / 10
        rows.append([i * 60000, str(close), str(close + 1), str(close - 1),
                     str(close), "10", (i + 1) * 60000 - 1])
    return rows


def info(market):
    return {"symbols": [{"symbol": s, "quoteAsset": "USDT", "status": "TRADING",
                         "contractType": "PERPETUAL", "isSpotTradingAllowed": True}
                        for s in ("AAAUSDT", "BBBUSDT", "CCCUSDT")]}


def tickers():
    return [{"symbol": s, "quoteVolume": str(v), "priceChangePercent": "2"}
            for s, v in (("AAAUSDT", 300), ("BBBUSDT", 200), ("CCCUSDT", 100))]


def fixture_response(request):
    path = request.url.path
    market = "spot" if request.url.host == "api.binance.com" else "futures"
    symbol = request.url.params.get("symbol")
    if path.endswith("exchangeInfo"):
        data = info(market)
    elif path.endswith("ticker/24hr"):
        data = tickers()
    elif path.endswith("klines"):
        data = bars(-1 if symbol == "AAAUSDT" else 1)
    elif path.endswith("depth"):
        data = {"bids": [["100", "2"]], "asks": [["100.01", "3"]]}
    elif path.endswith("openInterest"):
        data = {"symbol": symbol, "openInterest": "123", "time": 1000}
    elif path.endswith("premiumIndex"):
        data = {"symbol": symbol, "markPrice": "100", "lastFundingRate": "0.0001", "time": 1000}
    else:
        raise AssertionError("Unexpected endpoint " + path)
    return httpx.Response(200, json=data, headers={"X-MBX-USED-WEIGHT-1M": "120"})


async def with_context(fn, handler=fixture_response, snapshot=None, budget=6):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ctx = Context(freeze(snapshot or {}), client, 10, budget)
        return await fn(ctx)


@pytest.mark.parametrize("market,expected", [("spot", 100), ("futures", 41)])
def test_client_provenance_weights(market, expected):
    async def run(ctx):
        api = BinanceData(ctx, market)
        await api.exchange_info()
        result = await api.tickers()
        assert api.weight_used == expected and ctx.used == 2
        assert result.provenance["market"] == market
        assert result.provenance["params"] == {}
        assert result.provenance["fetched_at"]
        assert result.provenance["source_url"].startswith("https://")
        assert api.header_weight == 120
    asyncio.run(with_context(run))


@pytest.mark.parametrize("status,error", [(403, GeoBlocked), (451, GeoBlocked),
                                           (418, RateLimited), (429, RateLimited),
                                           (503, NetworkError), (400, Malformed)])
def test_taxonomy(status, error):
    async def run(ctx):
        with pytest.raises(error):
            await BinanceData(ctx, retries=0).exchange_info()
        assert ctx.used == 1 and ctx.provenance[0]["http_status"] == status
    asyncio.run(with_context(run, lambda r: httpx.Response(status)))


def test_network_error():
    def fail(request):
        raise httpx.ConnectError("offline fixture", request=request)
    async def run(ctx):
        with pytest.raises(NetworkError):
            await BinanceData(ctx).exchange_info()
    asyncio.run(with_context(run, fail))


@pytest.mark.parametrize("body", [b"not-json", b"123", b'{"code":-1}'])
def test_malformed(body):
    async def run(ctx):
        with pytest.raises(Malformed):
            await BinanceData(ctx).exchange_info()
    asyncio.run(with_context(run, lambda r: httpx.Response(200, content=body)))


def test_retry_and_budget():
    calls, delays = [], []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "2"}) if len(calls) == 1 else fixture_response(request)
    async def sleep(delay):
        delays.append(delay)
    async def run(ctx):
        api = BinanceData(ctx, sleep=sleep)
        await api.exchange_info()
        assert ctx.used == 2 and api.weight_used == 2 and delays == [2]
        api.weight_limit = 2
        with pytest.raises(RateLimited):
            await api.exchange_info()
        assert ctx.used == 2
    asyncio.run(with_context(run, respond))


def test_header_guard_and_long_retry():
    async def run(ctx):
        api = BinanceData(ctx)
        api.header_weight = 2000
        with pytest.raises(RateLimited):
            await api.exchange_info()
        assert ctx.used == 0
        api.header_weight = 0
        with pytest.raises(RateLimited, match="backoff"):
            await api.exchange_info()
    asyncio.run(with_context(run, lambda r: httpx.Response(429, headers={"Retry-After": "99"})))


def test_allowlist():
    async def run(ctx):
        for endpoint, params in (("order", {}), ("klines", {"symbol": "AAA", "interval": "1d", "limit": 60}),
                                 ("depth", {"symbol": "AAA", "limit": 5000}), ("exchangeInfo", {"signature": "x"})):
            with pytest.raises(ValueError):
                await BinanceData(ctx).read(endpoint, params)
        with pytest.raises(ValueError):
            await BinanceData(ctx, "spot").open_interest("AAA")
        assert ctx.used == 0
    asyncio.run(with_context(run))


@pytest.mark.parametrize("market", ["spot", "futures"])
def test_universe_priority_and_runtime_size(market):
    source = info(market)
    source["symbols"].append({"symbol": "OFF", "status": "BREAK"})
    symbols, ranked = universe(source, tickers(), market)
    assert len(symbols) == 3 and ranked[0]["symbol"] == "AAAUSDT"
    assert all(r["market"] == market and r["quote_group_n"] == 3 for r in ranked)
    diff = coverage_state({"universe": ["OFF", "AAAUSDT"]}, symbols)
    assert diff["instruments_added"] == 2 and diff["instruments_removed"] == 1
    assert coverage_state({}, symbols)["instruments_added"] is None


@pytest.mark.parametrize("market,budget,completed", [("spot", 4, 1), ("futures", 6, 1)])
def test_rotation_and_honest_coverage(market, budget, completed):
    async def run(ctx):
        result = await MarketScannerAgent(market).run(ctx)
        assert result["coverage"]["scanned_this_cycle"] == completed
        assert result["coverage"]["universe_size"] == 3
        assert result["coverage"]["fetches_used"] == budget
        assert result["candidates"][0]["market"] == market
        derivative = result["candidates"][0]["derivatives"]
        assert derivative["status"] == ("available" if market == "futures" else "not_applicable")
        state = result["state"]
        snapshot = {"scanner_state": {market: state}}
        again = await with_context(lambda c: MarketScannerAgent(market).run(c), snapshot=snapshot, budget=budget)
        assert again["coverage"]["cumulative_unique"] == 2
        assert again["coverage"]["scanned_symbols"] != result["coverage"]["scanned_symbols"]
        assert again["coverage"]["oldest_data_age_seconds"] >= 0
        assert again["coverage"]["instruments_added"] == 0
    asyncio.run(with_context(run, budget=budget))


@pytest.mark.parametrize("market", ["spot", "futures"])
def test_geoblock_empty_and_preserves_state(market, caplog):
    previous = {"seen": {"AAAUSDT": "2026-01-01T00:00:00+00:00"}, "universe": ["AAAUSDT"]}
    async def run(ctx):
        result = await MarketScannerAgent(market).run(ctx)
        assert result["status"] == "failed" and not result["candidates"]
        assert result["error"]["type"] == "GeoBlocked"
        assert result["coverage"]["scanned_this_cycle"] == 0
        assert result["coverage"]["universe_size"] is None
        assert result["coverage"]["cumulative_unique"] == 1
        assert dict(result["state"]["seen"]) == previous["seen"]
    asyncio.run(with_context(run, lambda r: httpx.Response(451), {"scanner_state": {market: previous}}))
    assert any(r.exc_info for r in caplog.records if "SCANNER_UNAVAILABLE" in r.message)


def test_zero_scan_budget_and_missing_ticker_coverage():
    async def run(ctx):
        result = await MarketScannerAgent("spot").run(ctx)
        assert result["status"] == "empty"
        assert result["coverage"]["scanned_this_cycle"] == 0
        assert result["coverage"]["cumulative_unique"] == 0
    asyncio.run(with_context(run, budget=2))


def test_indicator_math_structure_and_volume():
    rows = candles(bars(), 9999999999)
    result = indicators(rows)
    assert result["n"] == 60 and result["atr14"] == pytest.approx(2)
    assert result["momentum_pct"] == pytest.approx((105.9 / 105.4 - 1) * 100)
    assert result["volume_surge"] == 1
    assert result["volume_profile"]["base_volume"] == 600
    assert structure(rows)["structure"] == "higher_highs_higher_lows"
    assert structure(candles(bars(-1), 9999999999))["structure"] == "lower_highs_lower_lows"
    changed = bars()
    changed[-1][2], changed[-1][4], changed[-1][5] = "130", "129", "100"
    evidence = structure(candles(changed, 9999999999))
    assert evidence["breakout"] and len(evidence["heuristic_risk_flags"]) == 2
    assert all("HEURISTIC" in f for f in evidence["heuristic_risk_flags"])
    assert len(candles(bars(), 60 * 60)) == 60
    assert len(candles(bars(), 59 * 60)) == 59


@pytest.mark.parametrize("bad", [[], [[0]], [[0, "nan", 1, 1, 1, 1, 1]] * 25])
def test_invalid_klines(bad):
    with pytest.raises(Malformed):
        candles(bad, 9999999999)


def test_liquidity():
    row = liquidity({"bids": [[100, 2]], "asks": [[102, 3]]})
    assert row["spread_bps"] == pytest.approx(2 / 101 * 10000)
    assert row["bid_notional"] == 200 and row["n_ask_levels"] == 1
    with pytest.raises(Malformed):
        liquidity({"bids": [[102, 1]], "asks": [[100, 1]]})


def test_score_and_disagreement():
    up = {"momentum_pct": 1, "atr_pct": 1}
    down = {"momentum_pct": -1, "atr_pct": 1}
    score = directional_score([up] * 5)
    assert score["timeframe_agreement"] and score["n_timeframes"] == 5
    assert score["up_score"] + score["down_score"] == pytest.approx(1)
    assert not directional_score([up, down, up, up, up])["timeframe_agreement"]
    ordered, disagreements = challenges([{"symbol": "A", "market": "spot", "scanner_rank": 1, "up_score": .2},
                                         {"symbol": "B", "market": "spot", "scanner_rank": 3, "up_score": .8}])
    assert ordered[0]["symbol"] == "B" and len(disagreements) == 2
    assert all(r["n"] == 2 and r["market"] == "spot" for r in disagreements)


@pytest.mark.parametrize("market", ["spot", "futures"])
def test_ta_five_frames_budget_and_challenge(market):
    candidates = [{"market": market, "symbol": s} for s in ("AAAUSDT", "BBBUSDT")]
    snapshot = {"agents": {"scanner_" + market: {"data": {"candidates": candidates}}}}
    before = deepcopy(snapshot)
    async def run(ctx):
        result = await TechnicalAnalysisAgent(market).run(ctx)
        assert result["analyzed_count"] == 2 and ctx.used == 10
        assert len(result["disagreements"]) == 2
        assert set(result["candidates"][0]["frames"]) == set(TIMEFRAMES)
        assert result["candidates"][0]["market"] == market
    asyncio.run(with_context(run, snapshot=snapshot, budget=10))
    assert snapshot == before
    async def limited(ctx):
        result = await TechnicalAnalysisAgent(market).run(ctx)
        assert result["analyzed_count"] == 0 and ctx.used == 0
        assert result["status"] == "degraded"
    asyncio.run(with_context(limited, snapshot=snapshot, budget=4))


def test_persistent_rotation_checkpoint(tmp_path):
    previous = tmp_path / str(uuid4())
    previous.mkdir()
    state = {"spot": {"seen": {"AAAUSDT": "fixture"}, "universe": ["AAAUSDT"]}}
    (previous / "cycle.json").write_text(json.dumps({"scanner_state": state}))
    assert restored_state(LocalPersistence(tmp_path / str(uuid4()))) == state


def test_registry_sequencing_immutable_and_offline():
    async def run():
        registry = Registry()
        for market in ("spot", "futures"):
            registry.register(MarketScannerAgent(market))
            registry.register(TechnicalAnalysisAgent(market))
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture_response)) as client:
            result = await run_agents({}, registry=registry, client=client)
        assert result["technical_spot"]["data"]["analyzed_count"] == 1
        assert result["technical_futures"]["data"]["analyzed_count"] == 1
        assert all(r["status"] == "success" for r in result.values())
    asyncio.run(run())


def test_reporting_escape_separate_markets_empty_and_failed():
    empty = scanner_panel({})
    assert "Binance Spot" in empty and "USDT-M Futures" in empty
    assert "No completed candidates" in empty and "LAST STORED STATE" in empty
    report = {"agents": {"scanner_spot": {"data": {"as_of": "fixture", "status": "failed",
              "error": {"type": "GeoBlocked", "reason": "<script>"}, "candidates": []}}}}
    html = scanner_panel(report)
    assert "GeoBlocked" in html and "&lt;script&gt;" in html and "<script>" not in html
    assert "As of fixture" in html and "overflow-wrap:anywhere" in html

@pytest.mark.parametrize("market,base,prefix", [
    ("spot", "https://api.binance.com", "/api/v3/"),
    ("futures", "https://fapi.binance.com", "/fapi/v1/"),
])
def test_base_defaults(monkeypatch, market, base, prefix):
    monkeypatch.delenv(f"BINANCE_{market.upper()}_BASE_URL", raising=False)
    async def run(ctx):
        result = await BinanceData(ctx, market).exchange_info()
        assert result.provenance["base_url"] == base
        assert result.provenance["source_url"] == base + prefix + "exchangeInfo"
    asyncio.run(with_context(run))


@pytest.mark.parametrize("base", ["https://api.binance.com", "https://data-api.binance.vision"])
def test_spot_override_end_to_end(monkeypatch, base):
    monkeypatch.setenv("BINANCE_SPOT_BASE_URL", base)
    calls = []
    def respond(request):
        calls.append(request)
        return fixture_response(request)
    async def run(ctx):
        api = BinanceData(ctx, "spot")
        results = [await api.exchange_info(), await api.tickers(),
                   await api.klines("AAAUSDT", "1m"), await api.depth("AAAUSDT")]
        assert len(calls) == 4
        for request, result in zip(calls, results):
            assert str(request.url).startswith(base + "/api/v3/")
            assert result.provenance["base_url"] == base
            assert result.provenance in ctx.provenance
    asyncio.run(with_context(run, respond))


def test_futures_explicit_override(monkeypatch):
    monkeypatch.setenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com")
    async def run(ctx):
        result = await BinanceData(ctx).open_interest("AAAUSDT")
        assert result.provenance["base_url"] == "https://fapi.binance.com"
        assert result.provenance["source_url"].endswith("/fapi/v1/openInterest")
    asyncio.run(with_context(run))


@pytest.mark.parametrize("market", ["spot", "futures"])
@pytest.mark.parametrize("bad", [
    "", "http://api.binance.com", "https://example.com",
    "https://api.binance.com.evil.example", "https://" + "user" + "@" + "api.binance.com",
    "https://api.binance.com:443", "https://api.binance.com/",
    "https://api.binance.com/api/v3", "https://api.binance.com?x=1",
    "https://api.binance.com#fragment", " https://api.binance.com",
])
def test_invalid_base_rejected_before_fetch(monkeypatch, market, bad):
    monkeypatch.setenv(f"BINANCE_{market.upper()}_BASE_URL", bad)
    async def run(ctx):
        with pytest.raises(ValueError, match="official origin required"):
            BinanceData(ctx, market)
        assert ctx.used == 0 and not ctx.provenance
    asyncio.run(with_context(run))


def test_vision_not_futures(monkeypatch):
    monkeypatch.setenv("BINANCE_FUTURES_BASE_URL", "https://data-api.binance.vision")
    with pytest.raises(ValueError, match="official origin required"):
        BinanceData(None)


@pytest.mark.parametrize("status", [403, 451])
def test_override_geoblock_provenance(monkeypatch, status):
    monkeypatch.setenv("BINANCE_SPOT_BASE_URL", "https://data-api.binance.vision")
    async def run(ctx):
        with pytest.raises(GeoBlocked):
            await BinanceData(ctx, "spot").exchange_info()
        assert ctx.provenance[0]["base_url"] == "https://data-api.binance.vision"
        assert ctx.provenance[0]["http_status"] == status
    asyncio.run(with_context(run, lambda r: httpx.Response(status)))
