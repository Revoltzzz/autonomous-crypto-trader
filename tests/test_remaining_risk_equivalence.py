"""Synthetic PAPER ONLY risk-tail byte corpus, not observed market evidence."""
from dataclasses import replace
import difflib
import json
import math

import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, TradeProposal
from act.research.dispatch_safety import decision_bytes
from act.research.early_risk import UnsupportedPolicy
from act.research.remaining_risk import evaluate_remaining_risk
from act.research.semantic_capture import capture_policy
from act.risk.limits import RiskLimits, check_trade


# Zero costs make these actual binary-float thresholds exactly representable.
# Default-cost fixtures below separately cover captured nonzero cost arithmetic.
CASES = [
    ("per_trade", {}, {"equity": 400}, "Trade risk"),
    ("per_trade_adjacent", {}, {"equity": math.nextafter(500.0, 0)}, "Trade risk"),
    ("daily_realized", {}, {"daily_realized_pnl": -300}, "Daily loss"),
    ("daily_equity", {}, {"equity": 9700}, "Daily loss"),
    ("daily_exposure", {}, {"open_risk_usd": 296}, "Daily loss"),
    ("daily_adjacent", {}, {"open_risk_usd": math.nextafter(295.0, math.inf)}, "Daily loss"),
    ("drawdown_realized", {}, {"peak_equity": 12000}, "Drawdown"),
    ("drawdown_exposure", {}, {"peak_equity": 11110}, "Drawdown"),
    ("early_before_all", {"leverage": 2}, {"cash": 0, "equity": 400}, "No leverage"),
    ("stop_before_funded", {"stop_loss": None}, {"cash": 0}, "A stop-loss"),
    ("long_stop_before_funded", {"stop_loss": 100}, {"cash": 0}, "Long stop-loss"),
    ("tp_before_cash", {"take_profit": 100}, {"cash": 0}, "Take profit"),
    ("cash_before_trade", {}, {"cash": 0, "equity": 400}, "Insufficient cash"),
    ("trade_before_daily", {}, {"equity": 400, "daily_realized_pnl": -300}, "Trade risk"),
    ("daily_before_drawdown", {}, {"daily_realized_pnl": -300, "peak_equity": 12000}, "Daily loss"),
    ("trade_equal_reaches_daily", {}, {"equity": 500}, "Daily loss"),
    ("daily_equal_reaches_drawdown", {}, {"open_risk_usd": 295, "peak_equity": 11110}, "Drawdown"),
]


def inputs(p=None, a=None, costs=False):
    settings = Settings() if costs else Settings(fee_pct=0, slippage_pct=0)
    snapshot = capture_policy(settings, "solana", at="2026-11-19T12:30:00Z")
    proposal = TradeProposal(**({"token": "synthetic-offline", "entry_price": 100,
                                "size": 1, "stop_loss": 95,
                                "reason": "synthetic part10"} | (p or {})))
    account = AccountState(**({"equity": 10000, "cash": 10000,
                              "day_start_equity": 10000, "peak_equity": 10000} | (a or {})))
    return settings, snapshot, proposal, account


def compare(settings, snapshot, proposal, account, reason):
    production = check_trade(proposal, account, RiskLimits(), fee_pct=settings.fee_pct,
                             slippage_pct=settings.slippage_pct).model_dump(mode="json")
    interpreted = evaluate_remaining_risk(snapshot, proposal.model_dump(mode="json"),
                                         account.model_dump(mode="json"))
    left, right = decision_bytes(production), decision_bytes(interpreted)
    diff = "\n".join(difflib.unified_diff(left.decode().splitlines(),
                                       right.decode().splitlines(),
                                       fromfile="production", tofile="snapshot"))
    assert production["accepted"] is False
    assert production["reason"].startswith(reason)
    assert left == right, f"DECISION_BYTE_DIVERGENCE\n{diff}"


@pytest.mark.parametrize("name,p,a,reason", CASES, ids=[c[0] for c in CASES])
def test_remaining_bytes(name, p, a, reason):
    compare(*inputs(p, a), reason)


@pytest.mark.parametrize("a,reason", [
    ({"equity": 400}, "Trade risk"),
    ({"daily_realized_pnl": -300}, "Daily loss"),
    ({"peak_equity": 12000}, "Drawdown"),
])
def test_default_cost_bytes(a, reason):
    compare(*inputs(a=a, costs=True), reason)


@pytest.mark.parametrize("a", [{}, {"open_risk_usd": 295},
                              {"equity": 900, "day_start_equity": 900,
                               "peak_equity": 1000, "open_risk_usd": 0}])
def test_acceptance_never_returned(a):
    _, snapshot, p, account = inputs(a=a)
    # Third case is inclusive drawdown rejection, tested separately below.
    if a.get("equity") == 900:
        compare(*inputs(a=a), "Drawdown")
        return
    with pytest.raises(UnsupportedPolicy, match="NOT implemented"):
        evaluate_remaining_risk(snapshot, p.model_dump(mode="json"),
                                account.model_dump(mode="json"))


@pytest.mark.parametrize("old,new", [
    ("risk > account.equity", "risk >= account.equity"),
    ("daily_loss >= daily_cap", "daily_loss > daily_cap"),
    ("drawdown >= drawdown_cap", "drawdown > drawdown_cap"),
    ("Trade risk exceeds", "MUTATED trade risk"),
    ("Daily loss or projected", "MUTATED daily or projected"),
    ("Drawdown or projected", "MUTATED drawdown or projected"),
])
def test_guard_and_reason_drift_rejected(old, new):
    _, snapshot, p, account = inputs(a={"equity": 400})
    policy = snapshot.policy
    rules = policy["selection"]["named_production_rules"]["act.risk.limits"]["definitions"]
    key = "act.risk.limits.check_trade"
    assert old in rules[key]
    rules[key] = rules[key].replace(old, new)
    changed = replace(snapshot, canonical=json.dumps(policy))
    with pytest.raises(UnsupportedPolicy, match="drift"):
        evaluate_remaining_risk(changed, p.model_dump(mode="json"),
                                account.model_dump(mode="json"))


@pytest.mark.parametrize("kind", ["missing", "nonfinite", "spot", "dispatch"])
def test_unsupported_loud(kind):
    _, snapshot, p, account = inputs()
    proposal = p.model_dump(mode="json")
    policy = snapshot.policy
    if kind == "missing":
        proposal.pop("entry_price")
    elif kind == "nonfinite":
        proposal["entry_price"] = math.nan
    elif kind == "spot":
        snapshot = replace(snapshot, market="spot")
    else:
        policy["selection"]["dispatch"] = "on"
        snapshot = replace(snapshot, canonical=json.dumps(policy))
    with pytest.raises(UnsupportedPolicy):
        evaluate_remaining_risk(snapshot, proposal, account.model_dump(mode="json"))
