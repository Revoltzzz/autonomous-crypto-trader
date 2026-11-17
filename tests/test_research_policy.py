"""Offline fixtures for the standalone, deliberately unactivated resolver."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from act.research.policy import canonical_policy, register_policy, resolve_policy
from act.research.registry import MARKETS, StrategyRegistry, baseline_registry, encoded

AT = "2026-11-18T00:00:00+00:00"
POLICY = {
    "selection": {"minimum": 10000},
    "entry": {"mode": "fixture-only"},
    "exit": {"stop": 0.15, "target": 0.4},
    "costs": {"fee": 0.0025, "slippage": 0.005},
}


def state():
    registry = StrategyRegistry()
    for market in MARKETS:
        version = register_policy(registry, market, POLICY, "fixture-v1", "test", AT)
        registry.baseline(version)
    return {"paper_only": True, "events": registry.events, "champions": registry.champions}


def resolve(value, **kwargs):
    return resolve_policy(value, "solana", expected_policy=POLICY,
                          implementation=kwargs.get("implementation", "fixture-v1"))


def test_round_trip_bytes_and_immutable_exports():
    value = state()
    snapshot = resolve(value)
    assert snapshot.bytes == encoded(POLICY).encode("utf-8")
    assert snapshot.version_id == value["champions"]["solana"]
    assert snapshot.market == "solana"
    detached = snapshot.policy
    detached["exit"]["stop"] = 99
    value["events"].clear()
    assert snapshot.policy == POLICY
    with pytest.raises(FrozenInstanceError):
        snapshot.market = "spot"


def test_registry_owns_input_and_reloads_identical_bytes():
    registry = StrategyRegistry()
    source = deepcopy(POLICY)
    version = register_policy(registry, "solana", source, "fixture-v1", "test", AT)
    source["entry"]["mode"] = "changed"
    restored = StrategyRegistry(registry.events)
    assert restored.versions[version]["parameters"]["policy"] == POLICY
    exported = restored.versions
    exported[version]["parameters"]["policy"]["exit"].clear()
    assert registry.versions == restored.versions


def test_pointer_mismatch_fails():
    value = state()
    value["champions"]["solana"] = value["champions"]["spot"]
    with pytest.raises(ValueError, match="pointer"):
        resolve(value)


def test_missing_market_fails():
    value = state()
    value["events"] = [e for e in value["events"] if not (
        e["kind"] == "baseline" and e["market"] == "futures")]
    with pytest.raises(ValueError, match="Every market"):
        resolve(value)


def test_tampered_snapshot_fails_content_identity():
    value = state()
    value["events"][0]["version"]["parameters"]["policy"]["exit"]["stop"] = 0.1
    with pytest.raises(ValueError, match="identity"):
        resolve(value)


def test_policy_drift_fails():
    expected = deepcopy(POLICY)
    expected["exit"]["stop"] = 0.1
    with pytest.raises(ValueError, match="Policy drift"):
        resolve_policy(state(), "solana", expected_policy=expected, implementation="fixture-v1")


def test_implementation_drift_fails():
    with pytest.raises(ValueError, match="Implementation drift"):
        resolve(state(), implementation="fixture-v2")


def test_legacy_checkpoint_is_not_silently_upgraded():
    registry = baseline_registry(AT)
    value = {"paper_only": True, "events": registry.events, "champions": registry.champions}
    with pytest.raises(ValueError, match="legacy identity"):
        resolve(value)


@pytest.mark.parametrize("policy", [None, {}, {"entry": {}},
    dict(POLICY, exit={}), dict(POLICY, costs={"fee": float("nan")}),
    dict(POLICY, extra={"unused": True})])
def test_incomplete_or_nonfinite_policy_rejected(policy):
    with pytest.raises(ValueError):
        canonical_policy(policy)


def test_nonpaper_and_unknown_market_rejected():
    value = state()
    value["paper_only"] = False
    with pytest.raises(ValueError, match="PAPER ONLY"):
        resolve(value)
    with pytest.raises(ValueError, match="known market"):
        resolve_policy(state(), "unknown", expected_policy=POLICY, implementation="fixture-v1")


def test_register_never_moves_champion():
    value = state()
    registry = StrategyRegistry(value["events"])
    parent = registry.champions["solana"]
    register_policy(registry, "solana", POLICY, "fixture-v2", "candidate", AT, parent)
    assert registry.champions == value["champions"]


def test_implementation_required():
    with pytest.raises(ValueError, match="Implementation identity"):
        register_policy(StrategyRegistry(), "solana", POLICY, "", "test", AT)
