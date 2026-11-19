"""Synthetic PAPER ONLY TP/cash corpus; no observed-market or sizing proof."""
from dataclasses import replace
import difflib
import json

import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, TradeProposal
from act.research.dispatch_safety import decision_bytes
from act.research.early_risk import UnsupportedPolicy
from act.research.funded_risk import evaluate_funded_risk
from act.research.semantic_capture import capture_policy
from act.risk.limits import RiskLimits, check_trade


CASES = [
    ("tp_below", {"take_profit": 100}, 10000),
    ("tp_slipped_equal", {"take_profit": 100 * (1 + 0.5 / 100)}, 10000),
    ("tp_below_edge", {"take_profit": 100.4999999}, 10000),
    ("cash_zero", {}, 0),
    ("cash_below_edge", {}, 100.7512499),
    ("tp_then_cash", {"take_profit": 100}, 0),
    ("early_then_tp", {"leverage": 2, "take_profit": 100}, 0),
    ("cash_with_tp_above_edge", {"take_profit": 100.5000001}, 0),
]


def inputs(changes=None, cash=10000):
    settings = Settings()
    snapshot = capture_policy(settings, "solana", at="2026-11-19T12:00:00Z")
    proposal = TradeProposal(**({"token": "synthetic-offline", "entry_price": 100,
                                "size": 1, "stop_loss": 95,
                                "reason": "synthetic part9 n=8"} | (changes or {})))
    account = AccountState(equity=10000, cash=cash,
                           day_start_equity=10000, peak_equity=10000)
    return settings, snapshot, proposal, account


@pytest.mark.parametrize("name,changes,cash", CASES, ids=[c[0] for c in CASES])
def test_funded_risk_bytes(name, changes, cash):
    settings, snapshot, proposal, account = inputs(changes, cash)
    production = check_trade(proposal, account, RiskLimits(), fee_pct=settings.fee_pct,
                             slippage_pct=settings.slippage_pct).model_dump(mode="json")
    interpreted = evaluate_funded_risk(snapshot, proposal.model_dump(mode="json"),
                                      account.model_dump(mode="json"))
    left, right = decision_bytes(production), decision_bytes(interpreted)
    diff = "\n".join(difflib.unified_diff(left.decode().splitlines(),
                                       right.decode().splitlines(),
                                       fromfile="production", tofile="snapshot"))
    assert production["accepted"] is False
    assert left == right, f"DECISION_BYTE_DIVERGENCE {name}\n{diff}"


@pytest.mark.parametrize("cash", [100 * (1 + 0.5 / 100) * (1 + 0.25 / 100), 100.7512501, 10000])
def test_funded_boundary_is_not_an_accept(cash):
    _, snapshot, proposal, account = inputs(cash=cash)
    with pytest.raises(UnsupportedPolicy, match="NOT implemented"):
        evaluate_funded_risk(snapshot, proposal.model_dump(mode="json"),
                             account.model_dump(mode="json"))


@pytest.mark.parametrize("old,new", [
    ("cost > account.cash", "cost >= account.cash"),
    ("entry * proposal.size * (1 + fee_pct / 100)",
     "entry * proposal.size * (1 - fee_pct / 100)"),
])
def test_funded_source_drift_is_loud(old, new):
    _, snapshot, proposal, account = inputs(cash=0)
    policy = snapshot.policy
    rules = policy["selection"]["named_production_rules"]["act.risk.limits"]["definitions"]
    key = "act.risk.limits.check_trade"
    assert old in rules[key]
    rules[key] = rules[key].replace(old, new)
    changed = replace(snapshot, canonical=json.dumps(policy))
    with pytest.raises(UnsupportedPolicy, match="drift"):
        evaluate_funded_risk(changed, proposal.model_dump(mode="json"),
                             account.model_dump(mode="json"))


def test_funded_captured_reason_consumed():
    _, snapshot, proposal, account = inputs(cash=0)
    policy = snapshot.policy
    rules = policy["selection"]["named_production_rules"]["act.risk.limits"]["definitions"]
    key = "act.risk.limits.check_trade"
    rules[key] = rules[key].replace("Insufficient cash; borrowing and leverage forbidden",
                                   "MUTATED cash veto")
    changed = replace(snapshot, canonical=json.dumps(policy))
    assert evaluate_funded_risk(changed, proposal.model_dump(mode="json"),
                               account.model_dump(mode="json"))["reason"] == "MUTATED cash veto"
