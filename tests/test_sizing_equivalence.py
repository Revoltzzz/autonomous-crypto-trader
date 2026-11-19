"""Synthetic PAPER ONLY six-field sizing bytes, not full decision equivalence."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import difflib
import json
import math
import sys

import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, SecurityAssessment, TokenDiscovery
from act.research.dispatch_safety import decision_bytes
from act.research.early_risk import UnsupportedPolicy
from act.research.semantic_capture import capture_policy
from act.research.sizing import FIELDS, evaluate_sizing
from act.strategy import StrategySettings, evaluate


AT = datetime(2026, 11, 19, 18, 30, tzinfo=timezone.utc)
# Exact binary entry/stop/equity, and nextafter neighbors of actual float thresholds.
# The production 0.01 and 1e-10 constants are preserved, never rounded away.
EDGE = (1024 * .01 / (2 - 1.5)) * 2 / 1024
CASES = [
    ("cap", 1024, 2, 1.5, .03125, 0, 0),
    ("nominal_risk_tie", 1024, 2, 1.5, .5, 0, 0),
    ("funded_risk", 1024, 2, 1.5, .5, 12.5, 0),
    ("slipped_cap", 1024, 2, 1.5, .03125, 0, 12.5),
    ("both_costs", 1024, 2, 1.5, .03125, 12.5, 12.5),
    ("tiny_positive", 2 ** -30, 2, 1.5, .03125, 0, 0),
    ("entry_below", 1024, math.nextafter(2., 0), 1.5, .03125, 0, 0),
    ("entry_above", 1024, math.nextafter(2., math.inf), 1.5, .03125, 0, 0),
    ("stop_below", 1024, 2, math.nextafter(1.5, 0), .5, 0, 0),
    ("stop_above", 1024, 2, math.nextafter(1.5, math.inf), .5, 0, 0),
    ("cap_below", 1024, 2, 1.5, math.nextafter(EDGE, 0), 0, 0),
    ("cap_tie", 1024, 2, 1.5, EDGE, 0, 0),
    ("cap_above", 1024, 2, 1.5, math.nextafter(EDGE, math.inf), 0, 0),
]


def snapshot(monkeypatch, fraction=.03125, fee=0, slip=0):
    monkeypatch.setenv("RISK_MAX_POSITION_NOTIONAL_FRACTION", str(fraction))
    monkeypatch.setenv("SECURITY_ONCHAIN_ENABLED", "false")
    return capture_policy(Settings(_env_file=None, fee_pct=fee, slippage_pct=slip),
                          "solana", at=AT.isoformat())


@pytest.mark.parametrize("name,equity,entry,stop,fraction,fee,slip", CASES,
                         ids=[c[0] for c in CASES])
def test_sizing_bytes(monkeypatch, name, equity, entry, stop, fraction, fee, slip):
    captured = snapshot(monkeypatch, fraction, fee, slip)
    account = AccountState(equity=equity, cash=equity * 2,
                           day_start_equity=equity, peak_equity=equity)
    pool = TokenDiscovery(symbol="SYNTH", mint_address="synthetic", pool_address="pool",
        first_seen_at=AT - timedelta(hours=1), price_usd=entry,
        liquidity_usd=10000, volume_24h_usd=25000, buys_24h=200, sells_24h=100,
        source_url="https://example.org/synthetic")
    security = SecurityAssessment(score=1, lp_locked_pct=100,
        mint_authority_revoked=True, freeze_authority_revoked=True,
        top_holder_concentration_pct=1, source="synthetic offline")
    observed = {}
    # Observe unchanged production locals on return. No code replacement, source
    # execution, monkeypatch of decisions, or shared arithmetic with interpreter.
    def observe(frame, event, arg):
        if frame.f_code is evaluate.__code__ and event == "return":
            observed.update({k: frame.f_locals[k] for k in FIELDS})
            observed["stop"] = frame.f_locals["stop"]
    previous = sys.getprofile()
    try:
        sys.setprofile(observe)
        decision = evaluate(pool, security, cycle_id="synthetic", at=AT, account=account,
            fee=fee / 100, slippage=slip / 100,
            config=StrategySettings(stop_fraction=.5), volume_profile={
                "status": "available", "val": stop, "vah": entry, "poc": stop})
    finally:
        sys.setprofile(previous)
    assert observed.pop("stop") == stop  # Upstream stop must not silently change.
    assert decision.decision == "BUY", decision.failure_reasons
    interpreted = evaluate_sizing(captured, {"entry_price": entry, "stop_loss": stop},
                                  account.model_dump(mode="json"))
    left, right = decision_bytes(observed), decision_bytes(interpreted)
    diff = "\n".join(difflib.unified_diff(left.decode().splitlines(),
        right.decode().splitlines(), fromfile="production-sizing", tofile="snapshot-sizing"))
    assert left == right, f"DECISION_BYTE_DIVERGENCE {name}\n{diff}"
    assert decision.proposal["size"] == interpreted["size"]
    unshrunk = min(observed["nominal_notional"] / entry,
                  observed["risk_budget"] / (observed["entry_cost"] - observed["stop_proceeds"]),
                  observed["notional_cap"] / (entry * (1 + slip / 100)))
    assert interpreted["size"] == unshrunk * (1 - 1e-10)
    assert interpreted["size"] < unshrunk
    if name.startswith("cap") or name == "tiny_positive":
        assert interpreted["size"] * entry * (1 + slip / 100) <= observed["notional_cap"]


@pytest.mark.parametrize("old,new", [
    ("* 0.01", "* 0.02"),
    ("size=size", "size=round(size, 2)"),
    ("trade = TradeProposal", "size = round(size, 2)\n        trade = TradeProposal"),
    ("min(nominal_notional", "max(nominal_notional"),
    ("1 - 1e-10", "1 - 1e-9"),
    ("entry * (1 + slippage)", "entry * (1 - slippage)"),
])
def test_sizing_drift(monkeypatch, old, new):
    captured = snapshot(monkeypatch)
    policy = captured.policy
    rules = policy["selection"]["named_production_rules"]["act.strategy"]["definitions"]
    key = "act.strategy.evaluate"
    assert old in rules[key]
    rules[key] = rules[key].replace(old, new)
    with pytest.raises(UnsupportedPolicy, match="drift"):
        evaluate_sizing(replace(captured, canonical=json.dumps(policy)),
                        {"entry_price": 2, "stop_loss": 1.5}, {"equity": 1024})


@pytest.mark.parametrize("kind", ["market", "dispatch", "missing_body", "missing_value",
    "minimums", "zero_equity", "nonfinite", "stop_equal", "missing_stop"])
def test_sizing_unsupported(monkeypatch, kind):
    captured = snapshot(monkeypatch)
    policy = captured.policy
    p, a = {"entry_price": 2, "stop_loss": 1.5}, {"equity": 1024}
    if kind == "market":
        captured = replace(captured, market="spot")
    elif kind == "dispatch":
        policy["selection"]["dispatch"] = "on"
    elif kind == "missing_body":
        policy["selection"]["named_production_rules"]["act.strategy"]["definitions"].pop(
            "act.strategy.evaluate")
    elif kind == "missing_value":
        policy["selection"]["models"]["RiskSettings"]["values"].clear()
    elif kind == "minimums":
        policy["entry"]["instrument_minimums"] = {"status": "available"}
    elif kind == "zero_equity":
        a["equity"] = 0
    elif kind == "nonfinite":
        a["equity"] = math.inf
    elif kind == "stop_equal":
        p["stop_loss"] = 2
    else:
        p.pop("stop_loss")
    with pytest.raises(UnsupportedPolicy):
        evaluate_sizing(replace(captured, canonical=json.dumps(policy)), p, a)
