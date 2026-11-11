"""Synthetic, hand-computed TA and evidence contracts. No network."""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from act.intelligence import (IntelligenceSettings, WINDOWS, attach_intelligence,
                              intelligence_panel, technical)
from act.discovery import SOURCES, merge_candidates, normalize_pair
from act.persistence import SupabasePersistence
from test_strategy import pool
from test_volume_profile import fetch


def profile(closes=None):
    closes = list(range(1, 31)) if closes is None else closes
    return dict(status="available", timeframe="15m", poc=20,
                source_url="https://example.invalid/recorded-ohlcv", fetched_at="2026-11-11T00:00:00+00:00",
                ohlcv_rows=[[900 * i, p, p + 1, p - .5, p, 20 if i >= 25 else 10]
                            for i, p in enumerate(closes)])


def indicators(p=None, **kwargs):
    return technical(p if p is not None else profile(), **kwargs)["indicators"]


def test_linear_hand_computed_indicators():
    # SMA-seeded EMAs rise exactly 1 each step: EMA9=26, EMA21=20.
    # Every true range after first candle is 2. POC20 last touched at bar20.
    expected = dict(rsi14=100, ema9=26, ema21=20, ema9_slope=26/25-1,
                    ema21_slope=20/19-1, ema9_vs_ema21=.3, momentum5=.2,
                    atr14_normalized=2/30, volume_ratio5=2,
                    drawdown_from_high=30/31-1, bars_since_poc_touch=10)
    result = technical(profile())
    assert result["version"] == "ta-v1" and result["bar_count"] == 30
    assert set(expected) == set(WINDOWS)
    for name, value in expected.items():
        item = result["indicators"][name]
        assert item["available"] and item["reason"] is None
        assert item["value"] == pytest.approx(value)
        assert item["minimum_bars"] == WINDOWS[name]
        assert item["window_bars"] == 30
    assert result["metadata"]["timeframe"] == "15m"


def test_rsi_wilder_seed_and_one_update():
    # Fourteen changes: seven +1, seven -1 => RSI50; next +1 => 7.5/(7.5+6.5).
    closes = [100 + i % 2 for i in range(16)]
    assert indicators(profile(closes[:15]))["rsi14"]["value"] == 50
    assert indicators(profile(closes))["rsi14"]["value"] == pytest.approx(100*7.5/14)
    assert indicators(profile(list(range(30, 0, -1))))["rsi14"]["value"] == 0


def test_atr_wilder_one_update():
    p = profile([100]*16)
    # First fourteen TRs=1.5, final TR=3; Wilder=(14*1.5-1.5+3)/14.
    p["ohlcv_rows"][-1][2] = 102.5
    assert indicators(p)["atr14_normalized"]["value"] == pytest.approx(22.5/14/100)


def test_flat_and_zero_volume_are_not_fabricated():
    p = profile([100]*30)
    items = indicators(p)
    assert not items["rsi14"]["available"] and items["rsi14"]["value"] is None
    assert "undefined" in items["rsi14"]["reason"]
    assert items["momentum5"]["value"] == 0  # Genuine computed zero is permitted.
    for row in p["ohlcv_rows"]:
        row[5] = 0
    assert indicators(p)["volume_ratio5"]["reason"] == "earlier volume is zero"
    assert indicators(p)["volume_ratio5"]["value"] is None
    assert not items["bars_since_poc_touch"]["available"]


def test_provider_order_and_input_immutability():
    p = profile()
    before = copy.deepcopy(p)
    expected = technical(p)
    p["ohlcv_rows"].reverse()
    assert technical(p) == expected
    p["ohlcv_rows"].reverse()
    assert p == before


@pytest.mark.parametrize("rows", [[], None, [[1, 1]], [[0, 2, 1, 2, 2, 1]],
    [[0, 1, 2, .5, 1, -1]], [[0, 1, 2, .5, 1, float("nan")]],
    [[0, 1, 2, .5, 1, 1], [0, 1, 2, .5, 1, 1]]])
def test_invalid_unavailable_not_zero(rows):
    p = profile()
    p["ohlcv_rows"] = rows
    items = indicators(p)
    assert all(not item["available"] and item["value"] is None for item in items.values())
    assert all("invalid OHLCV" in item["reason"] for item in items.values())


def test_patchy_window_unavailable():
    p = profile()
    del p["ohlcv_rows"][10]
    assert all("patchy" in item["reason"] for item in indicators(p).values())


def test_short_window_independent_availability():
    items = indicators(profile(list(range(1, 13))))
    assert items["ema9"]["available"] and items["volume_ratio5"]["available"]
    assert not items["ema21"]["available"] and not items["rsi14"]["available"]
    assert "12 < 15" in items["rsi14"]["reason"]
    assert indicators(profile([1]))["bars_since_poc_touch"]["value"] is None


def test_settings_and_unfetched_evidence(monkeypatch):
    assert IntelligenceSettings().ta_enabled and not IntelligenceSettings().gates_enabled
    monkeypatch.setenv("INTELLIGENCE_TA_ENABLED", "false")
    assert not IntelligenceSettings().ta_enabled
    assert all("disabled" in item["reason"] for item in indicators(enabled=False).values())
    assert all("preflight" in item["reason"] for item in indicators({}).values())
    assert all(item["reason"] == "HTTP 429 exhausted" for item in indicators(
        {"status": "unavailable", "reason": "HTTP 429 exhausted"}).values())
    with pytest.raises(ValueError):
        IntelligenceSettings(gates_enabled=True)


def test_attachment_stats_json_and_policy_unchanged():
    pools = [SimpleNamespace(mint_address="a", pool_address="pa", intelligence_metadata={}),
             SimpleNamespace(mint_address="b", pool_address="pb", intelligence_metadata={})]
    report = {"paper_only": True, "decisions": [
        {"mint_address": "a", "decision": "REJECT", "reasoning": "existing gate", "proposal": {"score": 1}},
        {"mint_address": "b", "decision": "WATCH", "proposal": None}]}
    before = copy.deepcopy(report)
    attach_intelligence(report, pools, {"pa": profile()}, at=datetime(2026, 11, 11, tzinfo=timezone.utc))
    for old, new in zip(before["decisions"], report["decisions"]):
        assert {k: v for k, v in new.items() if k != "proposal"} == {k: v for k, v in old.items() if k != "proposal"}
        assert {k: v for k, v in new["proposal"].items() if k != "intelligence"} == (old["proposal"] or {})
        assert new["proposal"]["intelligence"]["evidence_only"]
    assert report["intelligence_stats"]["ta_computed"] == 1
    assert report["intelligence_stats"]["candidates"] == 2
    assert report["intelligence_stats"]["available"]["rsi14"] == 1
    assert report["intelligence_stats"]["external_sentiment_requests"] == 0
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    # The actual existing Supabase RPC carries the intelligence inside payload JSON, no schema change.
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={})
    store = SupabasePersistence("https://example.invalid", "synthetic-test-key", transport=httpx.MockTransport(handler))
    store.save(report, None)
    assert captured[0]["payload"] == report


def test_dashboard_safe_and_historic_unknown(monkeypatch):
    from act import api
    report = {"decisions": [{"mint_address": "<script>", "proposal": {"intelligence": {"description": "<img onerror=bad>"}}}]}
    html = intelligence_panel(report)
    assert "&lt;script&gt;" in html and "&lt;img onerror=bad&gt;" in html
    assert "<script>" not in html and "not recorded for this cycle" in html
    monkeypatch.setattr(api, "LATEST", report)
    assert 'id="intelligence"' in api.dashboard()


def dex_row():
    return dict(chainId="solana", pairAddress="pool", pairCreatedAt=1700000000000,
                baseToken={"address": "mint", "name": "Synthetic", "symbol": "TEST"},
                quoteToken={"symbol": "SOL"}, priceUsd="1", liquidity={"usd": 100},
                volume={"h24": 200}, txns={"h24": {"buys": 2, "sells": 1}})


def test_metadata_unknown_absent_present_and_canonical_metrics():
    row = dex_row()
    p = normalize_pair(row, SOURCES[2])
    assert p.intelligence_metadata["pair:pool"]["socials_present"] is None
    row["info"] = {"socials": []}
    assert normalize_pair(row, SOURCES[2]).intelligence_metadata["pair:pool"]["socials_present"] is False
    row["info"]["socials"] = [{"type": "twitter", "url": "https://example.invalid"}]
    p = normalize_pair(row, SOURCES[2])
    assert p.intelligence_metadata["pair:pool"]["socials_present"] is True
    canonical = p.model_copy(deep=True, update={"sources": [SOURCES[0]],
        "liquidity_usd": 999, "intelligence_metadata": {}})
    kept, _, _ = merge_candidates([canonical, p], SOURCES, 40)
    assert kept[0].liquidity_usd == 999
    assert kept[0].intelligence_metadata == p.intelligence_metadata


def test_retained_candles_no_duplicate_request():
    at = datetime(2026, 11, 11, tzinfo=timezone.utc)
    end = int(at.timestamp())
    rows = [[end-(i+1)*900, 100, 101, 99, 100, 10] for i in range(30)]
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {"attributes": {"ohlcv_list": rows}}})
    result = fetch(handler, pool(first_seen_at=at-timedelta(hours=30)), at=at, twice=True)
    assert result["status"] == "available" and len(calls) == 1
    assert result["ohlcv_rows"] == [tuple(r) for r in rows]
    assert result["request_params"]["aggregate"] == 15
    assert result["evaluation_at"] == at.isoformat()
    technical(result)
    assert len(calls) == 1
