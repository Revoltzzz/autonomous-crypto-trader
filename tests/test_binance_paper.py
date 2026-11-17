"""Offline fixtures; never calls an exchange."""
import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from act.agents import binance_paper as bp
from act.binance_data import Response
from act.paper.ledger import PaperLedger

NOW = "2026-11-17T12:02:00+00:00"
DECISION = "2026-11-17T12:00:00+00:00"


def response(data, at=NOW):
    return Response(data, {"fetched_at": at, "source_url": "https://api.binance.com/fixture"})


def metadata(symbol="BTCUSDT", base="BTC"):
    return {"symbol": symbol, "baseAsset": base, "quoteAsset": "USDT", "status": "TRADING",
            "filters": [{"filterType": "LOT_SIZE", "minQty": ".001", "maxQty": "1000", "stepSize": ".001"},
                        {"filterType": "MIN_NOTIONAL", "minNotional": "5"}]}


def book():
    return response({"bids": [["99", "1"], ["98", "10"]], "asks": [["101", "1"], ["102", "10"]]})


def bars(at=NOW):
    end = int(bp.ms(at)) // 60000 * 60000
    return response([[end - (60-i)*60000, "100", "101", "99", "100", "1000",
                      end - (59-i)*60000 - 1] for i in range(60)], at)


def candidate(identity="a", market="spot", symbol="BTCUSDT"):
    f = {"last_close": 100, "atr14": 1, "atr_pct": 1, "momentum_pct": 1,
         "market_structure": {"support": 98, "resistance": 104, "evidence": {"last_open_time_ms": 1}}}
    return {"id": identity, "market": market, "symbol": symbol, "quote_asset": "USDT",
            "frames": {k: deepcopy(f) for k in bp.TIMEFRAMES}, "up_score": .8,
            "timeframe_agreement": True, "as_of": NOW, "decision_at": DECISION,
            "returns": [[i, (i % 7)*.001] for i in range(30)], "reasons": ["fixture TA"],
            "provenance": [{"fetched_at": NOW}]}


def approved(state=None):
    return bp.RiskManagementAgent().approve(state or bp.initial_state(), candidate(), metadata(), NOW)


def test_delayed_fill_spread_and_size_impact():
    price, evidence = bp.fill_price(book(), bars(), 2, DECISION, NOW)
    assert price == pytest.approx(101.5)
    assert evidence["impact"] == .5 and evidence["anchor"] == 100
    assert evidence["bar_open_ms"] > bp.ms(DECISION)
    assert evidence["model"] == bp.MODEL
    sell, _ = bp.fill_price(book(), bars(), 2, DECISION, NOW, sell=True)
    assert sell == pytest.approx(98.5)


@pytest.mark.parametrize("q,decision,at,match", [
    (20, DECISION, NOW, "INSUFFICIENT_DEPTH"), (1, NOW, NOW, "WAIT_LATENCY"),
    (1, DECISION, "2026-11-17T12:10:00+00:00", "STALE")])
def test_no_fabricated_fill(q, decision, at, match):
    with pytest.raises(ValueError, match=match):
        bp.fill_price(book(), bars(), q, decision, at)


@pytest.mark.parametrize("data", [{"bids": [], "asks": []},
    {"bids": [[102, 1]], "asks": [[101, 1]]}, {"bids": [[80, 2]], "asks": [[81, 2]]}])
def test_invalid_depth(data):
    with pytest.raises(ValueError):
        bp.fill_price(response(data), bars(), 1, DECISION, NOW)


@pytest.mark.parametrize("market,expected", [("spot", .001), ("futures", .0005)])
def test_fees_env(monkeypatch, market, expected):
    key = "BINANCE_" + market.upper() + "_TAKER_RATE"
    monkeypatch.delenv(key, raising=False)
    assert bp.fee_rate(market) == expected
    monkeypatch.setenv(key, ".002")
    assert bp.fee_rate(market) == .002
    for invalid in ("nan", "-1", ".02"):
        monkeypatch.setenv(key, invalid)
        with pytest.raises((ValueError, RuntimeError)):
            bp.fee_rate(market)


@pytest.mark.parametrize("q", [.0001, .001, 1001, .1001])
def test_exchange_minimums(q):
    with pytest.raises(ValueError):
        bp.minimums(q, 100, metadata())


def test_rounding_and_market_lot():
    assert bp.round_size(.1239, metadata()) == .123
    bp.minimums(.1, 100, metadata())
    m = metadata()
    m["filters"].append({"filterType": "MARKET_LOT_SIZE", "minQty": ".01", "maxQty": ".05", "stepSize": ".01"})
    with pytest.raises(ValueError, match="MARKET_LOT_SIZE"):
        bp.minimums(.1, 100, m)


def test_funding_timestamp_sign_idempotency_and_gap():
    p = {"symbol": "BTCUSDT", "size": 2, "opened_at": DECISION}
    rows = [{"symbol": "BTCUSDT", "fundingTime": bp.ms(DECISION), "fundingRate": ".001", "markPrice": "100"},
            {"symbol": "BTCUSDT", "fundingTime": bp.ms(NOW), "fundingRate": "-.001", "markPrice": "100"}]
    cost, evidence = bp.funding(p, rows, NOW)
    assert cost == -.2 and len(evidence) == 1
    p["funding_through"] = NOW
    assert bp.funding(p, rows, NOW) == (0, [])
    with pytest.raises(ValueError, match="GAP"):
        bp.funding({**p, "funding_through": "2026-11-16T00:00:00+00:00"}, rows, NOW)


def test_sizing_never_recovery_scaled():
    s = bp.initial_state()
    a = approved(s)
    assert a["size"]*100 <= 20000*.15
    assert a["size"]*(100-a["stop_loss"]+100*.022) <= 20000*.005
    s["accounts"]["spot"]["cash"] -= 100
    assert approved(s)["size"] <= a["size"]


@pytest.mark.parametrize("change,match", [({"halt": "critical"}, "EMERGENCY_HALT"),
    ({"daily_halt": "2026-11-17"}, "DAILY_LOSS_HALT"), ({"used": ["a"]}, "DUPLICATE")])
def test_halts_and_duplicates(change, match):
    s = bp.initial_state()
    s.update(change)
    with pytest.raises(ValueError, match=match):
        approved(s)


def test_daily_loss_halts_rest_of_day():
    s = bp.initial_state()
    s["accounts"]["spot"]["cash"] -= 401
    with pytest.raises(ValueError, match="DAILY_LOSS"):
        approved(s)
    assert s["daily_halt"] == "2026-11-17"
    s["accounts"]["spot"]["cash"] += 500
    with pytest.raises(ValueError, match="DAILY_LOSS"):
        approved(s)


@pytest.mark.parametrize("field,value,match", [("as_of", "2026-11-16T00:00:00+00:00", "STALE"),
    ("frames", {}, "INCOMPLETE"), ("timeframe_agreement", False, "DISAGREEMENT"),
    ("up_score", .4, "CORROBORATED"), ("quote_asset", "BTC", "UNSUPPORTED")])
def test_stale_incomplete_and_disagreement(field, value, match):
    c = candidate()
    c[field] = value
    with pytest.raises(ValueError, match=match):
        bp.RiskManagementAgent().approve(bp.initial_state(), c, metadata(), NOW)


def test_underlying_and_correlation_vetoes():
    s = bp.initial_state()
    p = bp.TradeExecutionSimulationAgent().open(s, approved(s), 100, {"anchor": 100}, NOW)
    with pytest.raises(ValueError, match="SAME_UNDERLYING"):
        bp.RiskManagementAgent().approve(s, candidate("b", "futures"), metadata(), NOW)
    for rs, expected in [([], "CORRELATION_UNVERIFIED"), (candidate()["returns"], "HIGH_CORRELATION")]:
        p["returns"] = rs
        with pytest.raises(ValueError, match=expected):
            bp.RiskManagementAgent().approve(s, candidate("b", symbol="ETHUSDT"), metadata("ETHUSDT", "ETH"), NOW)


def test_position_and_total_exposure_caps():
    s = bp.initial_state()
    s["pending"] = [{"id": str(i)} for i in range(4)]
    with pytest.raises(ValueError, match="MAX_SIMULTANEOUS"):
        approved(s)
    risk = bp.RiskManagementAgent(replace(bp.RiskLimits(), max_exposure=.001))
    with pytest.raises(ValueError, match="MAX_TOTAL_EXPOSURE"):
        risk.approve(bp.initial_state(), candidate(), metadata(), NOW)


def test_accounting_partial_full_exit_and_duplicate():
    s = bp.initial_state()
    ex = bp.TradeExecutionSimulationAgent()
    a = approved(s)
    p = ex.open(s, a, 100, {"anchor": 100}, NOW)
    assert bp.portfolio(s)["total"]["equity"] == pytest.approx(20000-p["fees_paid"])
    q = p["size"]
    t1 = ex.close(s, p, q/2, 110, {"anchor": 110}, NOW, "partial_profit")
    assert p["partial"] and p["size"] == q/2
    t2 = ex.close(s, p, p["size"], 105, {"anchor": 105}, "2026-11-17T12:03:00+00:00", "stop")
    assert not s["positions"]
    assert bp.portfolio(s)["total"]["equity"] == pytest.approx(20000+t1["realized_pnl"]+t2["realized_pnl"])
    assert s["accounts"]["futures"]["cash"] == 10000
    with pytest.raises(ValueError, match="DUPLICATE"):
        ex.open(s, a, 100, {}, NOW)


def test_missing_mark_total_unavailable():
    s = bp.initial_state()
    p = bp.TradeExecutionSimulationAgent().open(s, approved(s), 100, {}, NOW)
    p["mark"] = None
    assert bp.portfolio(s)["total"]["equity"] is None


def test_shared_ledger_preserves_solana(tmp_path):
    ledger = PaperLedger(tmp_path / "ledger.json")
    before = ledger.history
    s = bp.initial_state()
    ledger._append({"kind": "binance_paper", "at": NOW, "cycle_id": "test", "state": s})
    loaded = PaperLedger(ledger.path)
    assert bp.restore(loaded) == s and loaded.history[:len(before)] == before
    assert loaded.cash == 10000 and not loaded.positions and not loaded.closed_trades


def test_pipeline_queues_without_decision_tick_fill(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "stamp", lambda: NOW)
    async def info(self):
        return response({"symbols": [metadata()]})
    async def kline(self, symbol, interval):
        return bars()
    monkeypatch.setattr(bp.BinanceData, "exchange_info", info)
    monkeypatch.setattr(bp.BinanceData, "klines", kline)
    c = candidate()
    report = {"paper_only": True, "cycle_id": "fixture", "paper_account": {"equity": 10000, "cash": 10000},
              "agents": {"scanner_spot": {"data": {"candidates": [c]}}, "technical_spot": {"data": {"candidates": [c]}}}}
    ledger = PaperLedger(tmp_path / "ledger.json")
    s = asyncio.run(bp.run_pipeline(report, ledger))
    assert len(s["pending"]) == 1 and not s["positions"]
    assert report["portfolio_account"]["equity"] == 30000 and report["binance_learning"]["n"] == 1
    assert report["binance_paper"]["decisions"][0]["action"] == "APPROVE"
    with pytest.raises(RuntimeError, match="Duplicate Binance cycle"):
        asyncio.run(bp.run_pipeline(report, ledger))


def test_paper_guard_and_escaped_mobile_panel(tmp_path):
    with pytest.raises(ValueError, match="PAPER ONLY"):
        asyncio.run(bp.run_pipeline({}, PaperLedger(tmp_path / "x.json")))
    panel = bp.paper_panel({"binance_paper": {"as_of": "<script>", "positions": [], "risk": {"halt": "<img>"}, "closed_trades_n": 0}})
    assert "<script>" not in panel and "&lt;script&gt;" in panel
    assert "overflow-wrap:anywhere" in panel and "LAST STORED STATE" in panel


def test_completed_positions_does_not_inflate_partial_samples():
    s = bp.initial_state()
    ex = bp.TradeExecutionSimulationAgent()
    p = ex.open(s, approved(s), 100, {"anchor": 100}, NOW)
    ex.close(s, p, p["size"]/2, 110, {"anchor": 110}, NOW, "partial_profit")
    assert bp.completed_positions(s) == []
    ex.close(s, p, p["size"], 105, {"anchor": 105}, "2026-11-17T12:03:00+00:00", "stop")
    rows = bp.completed_positions(s)
    assert len(rows) == 1 and rows[0]["tranches_n"] == 2
    assert rows[0]["realized_pnl"] == pytest.approx(sum(t["realized_pnl"] for t in s["trades"]))


def test_pipeline_pending_fill_uses_post_decision_data(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "stamp", lambda: NOW)
    async def info(self):
        return response({"symbols": [metadata()]})
    async def kline(self, symbol, interval):
        value = bars()
        for i, row in enumerate(value.data):
            close = 94 + (i+1)*.1
            row[1:5] = [str(close-.05), str(close+.1), str(close-.1), str(close)]
        return value
    async def depth(self, symbol):
        return response({"bids": [["99.9", "1000"]], "asks": [["100.1", "1000"]]})
    monkeypatch.setattr(bp.BinanceData, "exchange_info", info)
    monkeypatch.setattr(bp.BinanceData, "klines", kline)
    monkeypatch.setattr(bp.BinanceData, "depth", depth)
    ledger = PaperLedger(tmp_path / "pipeline.json")
    s = bp.initial_state()
    s["pending"].append(approved(s))
    ledger._append({"kind": "binance_paper", "at": DECISION, "cycle_id": "seed", "state": s})
    report = {"paper_only": True, "cycle_id": "fill", "paper_account": {"equity": 10000, "cash": 10000}}
    result = asyncio.run(bp.run_pipeline(report, ledger))
    assert result["halt"] is None
    assert len(result["positions"]) == 1, report["binance_paper"]["decisions"]
    p = next(iter(result["positions"].values()))
    assert p["execution"]["bar_open_ms"] > bp.ms(DECISION)
    assert p["entry_price"] == pytest.approx(100.1)
    assert report["paper_positions"][0]["stop_loss"] < p["entry_price"]
    assert report["paper_positions"][0]["current_price"] is not None
