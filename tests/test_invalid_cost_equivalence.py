"""Synthetic invalid-cost veto bytes; captured costs deliberately mutated."""
from dataclasses import replace
import difflib
import json
import math

import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, TradeProposal
from act.research.dispatch_safety import decision_bytes
from act.research.early_risk import UnsupportedPolicy
from act.research.invalid_cost import evaluate_invalid_cost
from act.research.semantic_capture import capture_policy
from act.risk.limits import check_trade


@pytest.mark.parametrize("field,value", [
    (field, value) for field in ("fee_pct", "slippage_pct")
    for value in (math.nextafter(0., -math.inf), 100., math.nextafter(100., math.inf))
])
def test_invalid_cost_bytes(field, value):
    captured = capture_policy(Settings(_env_file=None), "solana",
                              at="2026-11-19T18:30:00Z")
    policy = captured.policy
    policy["costs"]["effective"][field] = value
    captured = replace(captured, canonical=json.dumps(policy, allow_nan=False))
    costs = captured.policy["costs"]["effective"]
    # Leverage and missing-stop prove cost veto precedence.
    proposal = TradeProposal(token="synthetic", entry_price=2, size=1,
                             leverage=2, stop_loss=None, reason="offline")
    account = AccountState(equity=1024, cash=1024,
                           day_start_equity=1024, peak_equity=1024)
    production = check_trade(proposal, account, fee_pct=costs["fee_pct"],
                             slippage_pct=costs["slippage_pct"]).model_dump(mode="json")
    left, right = decision_bytes(production), decision_bytes(evaluate_invalid_cost(captured))
    diff = "\n".join(difflib.unified_diff(left.decode().splitlines(),
        right.decode().splitlines(), fromfile="production", tofile="snapshot"))
    assert production["accepted"] is False
    assert production["reason"] == "Invalid fee or slippage assumption"
    assert left == right, f"DECISION_BYTE_DIVERGENCE\n{diff}"


@pytest.mark.parametrize("value", [0., math.nextafter(100., 0)])
def test_valid_cost_continuation_loud(value):
    captured = capture_policy(Settings(_env_file=None), "solana",
                              at="2026-11-19T18:30:00Z")
    policy = captured.policy
    policy["costs"]["effective"].update(fee_pct=value, slippage_pct=value)
    with pytest.raises(UnsupportedPolicy, match="NOT implemented"):
        evaluate_invalid_cost(replace(captured, canonical=json.dumps(policy)))


@pytest.mark.parametrize("kind", ["reason", "guard", "market", "dispatch", "missing"])
def test_invalid_cost_fail_closed(kind):
    captured = capture_policy(Settings(_env_file=None), "solana",
                              at="2026-11-19T18:30:00Z")
    policy = captured.policy
    policy["costs"]["effective"]["fee_pct"] = -1
    rules = policy["selection"]["named_production_rules"]["act.risk.limits"]["definitions"]
    key = "act.risk.limits.check_trade"
    if kind == "reason":
        rules[key] = rules[key].replace("Invalid fee or slippage assumption", "changed")
    elif kind == "guard":
        rules[key] = rules[key].replace("0 <= x < 100", "0 <= x <= 100")
    elif kind == "market":
        captured = replace(captured, market="spot")
    elif kind == "dispatch":
        policy["selection"]["dispatch"] = "on"
    else:
        rules.pop(key)
    with pytest.raises(UnsupportedPolicy):
        evaluate_invalid_cost(replace(captured, canonical=json.dumps(policy)))
