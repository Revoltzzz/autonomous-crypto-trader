"""Synthetic n=6 Solana EARLY RISK veto corpus; not full strategy decisions."""
from dataclasses import replace
import difflib
import json

import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, TradeProposal
from act.research.dispatch_safety import decision_bytes
from act.research.early_risk import UnsupportedPolicy, evaluate_early_risk
from act.research.semantic_capture import capture_policy
from act.risk.limits import RiskLimits, check_trade


CASES = [
    ("leverage", {"leverage": 2}),
    ("leverage_edge", {"leverage": 1.0000001}),
    ("missing_stop", {"stop_loss": None}),
    ("stop_equal_entry", {"stop_loss": 100}),
    ("stop_above_entry", {"stop_loss": 100.0000001}),
    ("veto_precedence", {"leverage": 2, "stop_loss": None}),
]


def capture():
    settings = Settings()
    return settings, capture_policy(settings, "solana", at="2026-11-19T11:30:00Z")


def proposal(**changes):
    return TradeProposal(**({"token": "synthetic-offline", "entry_price": 100,
                            "size": 1, "stop_loss": 95, "reason": "synthetic n=6"}
                           | changes))


@pytest.mark.parametrize("name,changes", CASES, ids=[c[0] for c in CASES])
def test_early_risk_bytes(name, changes):
    settings, snapshot = capture()
    candidate = proposal(**changes)
    account = AccountState(equity=10000, cash=10000,
                           day_start_equity=10000, peak_equity=10000)
    # Separate live objects vs owned captured JSON; no shared decision evaluator.
    hardcoded = check_trade(candidate, account, RiskLimits(), fee_pct=settings.fee_pct,
                            slippage_pct=settings.slippage_pct).model_dump(mode="json")
    interpreted = evaluate_early_risk(snapshot, candidate.model_dump(mode="json"))
    left, right = decision_bytes(hardcoded), decision_bytes(interpreted)
    diff = "\n".join(difflib.unified_diff(left.decode().splitlines(),
                                       right.decode().splitlines(),
                                       fromfile="production", tofile="snapshot"))
    assert left == right, f"DECISION_BYTE_DIVERGENCE {name}\n{diff}"


def test_unimplemented_acceptance_fails_loudly():
    _, snapshot = capture()
    with pytest.raises(UnsupportedPolicy, match="NOT implemented"):
        evaluate_early_risk(snapshot, proposal().model_dump(mode="json"))


def test_source_guard_mutation_fails_closed():
    _, snapshot = capture()
    policy = snapshot.policy
    row = policy["selection"]["named_production_rules"]["act.risk.limits"]["definitions"]
    key = "act.risk.limits.check_trade"
    row[key] = row[key].replace("proposal.leverage != 1", "proposal.leverage > 1")
    changed = replace(snapshot, canonical=json.dumps(policy))
    with pytest.raises(UnsupportedPolicy, match="guard drift"):
        evaluate_early_risk(changed, proposal(leverage=2).model_dump(mode="json"))


def test_captured_reason_is_consumed():
    _, snapshot = capture()
    policy = snapshot.policy
    row = policy["selection"]["named_production_rules"]["act.risk.limits"]["definitions"]
    key = "act.risk.limits.check_trade"
    row[key] = row[key].replace("No leverage permitted", "MUTATED veto")
    changed = replace(snapshot, canonical=json.dumps(policy))
    assert evaluate_early_risk(changed, proposal(leverage=2).model_dump(mode="json"))[
        "reason"] == "MUTATED veto"
