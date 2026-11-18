"""Offline, synthetic capture contract tests; NOT decision equivalence fixtures."""
from copy import deepcopy
import json

import httpx
import pytest

from act.config.settings import Settings
from act.research.policy import canonical_policy, register_policy, resolve_policy
from act.research.registry import MARKETS, StrategyRegistry
from act.research.semantic_capture import capture_policy, instrument_capture

AT = "2026-11-18T12:00:00+00:00"
IMPLEMENTATION = "test-source-backed-capture; not an activation proof"


@pytest.fixture
def settings(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Capture must not make HTTP requests")
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    return Settings(_env_file=None, supabase_service_key="DO_NOT_CAPTURE_THIS_SENTINEL")


def instrument():
    # Deliberately synthetic TESTONLY instrument, not recorded exchange evidence.
    return {"symbol": "TESTONLYUSDT", "status": "TRADING", "quoteAsset": "USDT",
            "baseAsset": "TESTONLY", "filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.010", "maxQty": "1000", "stepSize": "0.010"},
                {"filterType": "MARKET_LOT_SIZE", "minQty": "0.020", "maxQty": "200", "stepSize": "0.010"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "5.000"}]}


def observation():
    return {"status": "available", "source": "synthetic offline fixture; not exchange evidence",
            "fetched_at": AT, "symbols": [instrument()]}


def capture(settings, market):
    if market == "solana":
        return capture_policy(settings, market, at=AT)
    if market == "futures":
        return capture_policy(settings, market, at=AT, instruments={
            "status": "blocked_http_451", "source": "synthetic blocked-state fixture; not a live probe",
            "fetched_at": AT, "symbols": []})
    return capture_policy(settings, market, at=AT, instruments=observation(),
                          required_symbols=["TESTONLYUSDT"])


@pytest.mark.parametrize("market", MARKETS)
def test_captured_snapshot_passes_full_pr23_resolver(settings, market):
    snapshots = {m: capture(settings, m) for m in MARKETS}
    registry = StrategyRegistry()
    for m, snapshot in snapshots.items():
        identity = register_policy(registry, m, snapshot.policy, IMPLEMENTATION,
                                   "Synthetic offline resolver acceptance only", AT)
        registry.baseline(identity)
    state = {"paper_only": True, "events": registry.events, "champions": registry.champions}
    resolved = resolve_policy(state, market, expected_policy=snapshots[market].policy,
                              implementation=IMPLEMENTATION)
    assert resolved.canonical == snapshots[market].canonical
    assert set(resolved.policy) == {"selection", "entry", "exit", "costs"}
    assert all(resolved.policy[section] for section in resolved.policy)
    assert resolved.policy["selection"]["dispatch"] == "off"
    assert "DO_NOT_CAPTURE_THIS_SENTINEL" not in resolved.canonical


def test_named_rule_bodies_keep_boundaries_sizing_and_exit_priority(settings):
    p = capture(settings, "solana").policy
    inventory = p["selection"]["named_production_rules"]
    solana = inventory["act.strategy"]["definitions"]["act.strategy.evaluate"]
    assert "score >= cfg.max_rugcheck_score" in solana
    assert "risk_budget = account.equity * 0.01" in solana
    assert "cfg.min_buys_sells_ratio" in solana
    approve = inventory["act.agents.binance_paper"]["definitions"]["act.agents.binance_paper.RiskManagementAgent.approve"]
    assert "candidate['up_score'] < 0.6" in approve
    assert "support - 0.25 * atr" in approve
    assert "2 * rate + 0.02" in approve
    pipeline = inventory["act.agents.binance_paper"]["definitions"]["act.agents.binance_paper.run_pipeline"]
    for text in ("partial_profit", "trailing_or_stop_loss", "entry_support_broken", "take_profit"):
        assert text in pipeline
    assert "p['peak'] - 2 * p['atr']" in pipeline
    assert "1.5 * p['atr']" in pipeline
    assert inventory["act.agents.scanner"]["guards"]
    assert "ConcentrationSettings" in p["selection"]["models"]


def test_effective_overrides_and_sources(settings, monkeypatch):
    monkeypatch.setenv("STRATEGY_MIN_LIQUIDITY_USD", "12345")
    monkeypatch.setenv("SECURITY_CONCENTRATION_TOLERANCE_PP", "0.75")
    monkeypatch.setenv("BINANCE_SPOT_MOVER_TOP_N", "3")
    monkeypatch.setenv("BINANCE_SPOT_TAKER_RATE", "0.002")
    monkeypatch.setenv("AGENT_FETCH_BUDGET", "9")
    monkeypatch.setenv("AGENT_SCANNER_SPOT_FETCH_BUDGET", "4")
    p = capture(settings, "spot").policy
    cfg = p["selection"]["models"]["StrategySettings"]
    assert cfg["values"]["min_liquidity_usd"] == 12345
    assert cfg["sources"]["min_liquidity_usd"]["source"] == "environment"
    assert cfg["bounds"]["min_liquidity_usd"]["exclusiveMinimum"] == 0
    assert p["selection"]["models"]["ConcentrationSettings"]["values"]["security_concentration_tolerance_pp"] == .75
    controls = p["selection"]["controls"]
    assert controls["BINANCE_SPOT_MOVER_TOP_N"]["value"] == 3
    assert controls["AGENT_SCANNER_SPOT_FETCH_BUDGET"]["value"] == 4
    assert controls["AGENT_TECHNICAL_SPOT_FETCH_BUDGET"]["value"] == 9
    assert controls["AGENT_TECHNICAL_SPOT_FETCH_BUDGET"]["source"] == "AGENT_FETCH_BUDGET"
    assert p["costs"]["effective"]["taker"]["value"] == .002


def test_dynamic_minimums_are_exact_and_detached():
    original = observation()
    captured = instrument_capture("spot", original, ["TESTONLYUSDT"])
    assert captured["symbols"] == original["symbols"]
    original["symbols"][0]["filters"][0]["minQty"] = "99"
    assert captured["symbols"][0]["filters"][0]["minQty"] == "0.010"
    assert captured["symbols"][0]["filters"][1]["maxQty"] == "200"
    assert captured["symbols"][0]["filters"][2]["minNotional"] == "5.000"
    assert captured["tradable"] is False


def test_notional_precedence_and_all_filter_fields_retained():
    original = observation()
    original["symbols"][0]["filters"].append({"filterType": "NOTIONAL", "minNotional": "7.123",
                                           "maxNotional": "100.456", "applyMaxToMarket": True})
    captured = instrument_capture("spot", original, ["TESTONLYUSDT"])
    assert captured["symbols"] == original["symbols"]


@pytest.mark.parametrize("mutation", ["missing_lot", "missing_notional", "nan", "duplicate"])
def test_missing_or_malformed_dynamic_minimums_fail_loudly(mutation):
    source = observation()
    filters = source["symbols"][0]["filters"]
    if mutation == "missing_lot":
        filters.pop(0)
    elif mutation == "missing_notional":
        filters.pop()
    elif mutation == "nan":
        filters[0]["minQty"] = "NaN"
    else:
        filters.append(deepcopy(filters[0]))
    with pytest.raises((ValueError, KeyError)):
        instrument_capture("spot", source, ["TESTONLYUSDT"])


def test_missing_observation_scope_and_blocked_tradability_fail(settings):
    with pytest.raises(ValueError, match="DYNAMIC_INSTRUMENT_MINIMUMS_REQUIRED"):
        capture_policy(settings, "spot", at=AT)
    with pytest.raises(ValueError, match="scope mismatch"):
        instrument_capture("spot", observation(), ["OTHERUSDT"])
    blocked = {"status": "blocked_http_451", "source": "synthetic", "fetched_at": AT, "symbols": []}
    with pytest.raises(ValueError, match="Blocked capture"):
        instrument_capture("futures", blocked, ["TESTONLYUSDT"])
    p = capture(settings, "futures").policy
    assert p["selection"]["data_status"] == "blocked_http_451"
    assert p["entry"]["instrument_minimums"]["tradable"] is False


def test_immutable_capture_and_missing_sections(settings):
    snapshot = capture(settings, "solana")
    copy = snapshot.policy
    copy["selection"]["dispatch"] = "on"
    assert snapshot.policy["selection"]["dispatch"] == "off"
    for section in snapshot.policy:
        broken = json.loads(snapshot.canonical)
        broken[section] = {}
        with pytest.raises(ValueError, match="Every policy section"):
            canonical_policy(broken)


def test_invalid_effective_control_fails(settings, monkeypatch):
    monkeypatch.setenv("BINANCE_SPOT_MOVER_TOP_N", "11")
    with pytest.raises(ValueError, match="0..10"):
        capture(settings, "spot")
