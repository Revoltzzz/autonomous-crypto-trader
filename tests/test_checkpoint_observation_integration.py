"""Offline integration checks; no dispatch or strategy equivalence claim."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from act.research.checkpoint import capture_registry, validate
from act.research.dispatch_safety import effective_observation

AT = "2026-11-18T00:00:00Z"


def settings():
    return SimpleNamespace(paper_only=True, fee_pct=0.25, slippage_pct=0.5)


def capture(tmp_path):
    return capture_registry({"paper_only": True, "started_at": AT},
                            SimpleNamespace(directory=tmp_path), settings())


def save(tmp_path, state):
    (tmp_path / "cycle.json").write_text(
        json.dumps({"research_registry": state}), encoding="utf-8")


def test_checkpoint_observation_write_and_idempotence(tmp_path):
    state = capture(tmp_path)
    original = deepcopy(state)
    assert len(state["policy_observations"]) == 3
    assert validate(state).champions == state["champions"]
    save(tmp_path, state)
    again = capture(tmp_path)
    assert again["events"] == state["events"]
    assert again["champions"] == state["champions"]
    assert original == state


def test_legacy_checkpoint_appends_without_rewriting(tmp_path):
    first = capture(tmp_path)
    registry = validate(first)
    baseline_ids = set(first["champions"].values())
    legacy_events = []
    for event in first["events"]:
        legacy_events.append(event)
        if set(validate_event_ids(legacy_events, registry)) == baseline_ids:
            break
    legacy = {key: deepcopy(first[key]) for key in (
        "schema", "paper_only", "activated_at", "champions",
        "registration_cost_settings")}
    legacy["events"] = legacy_events
    validate(legacy)
    before = deepcopy(legacy)
    save(tmp_path, legacy)
    migrated = capture(tmp_path)
    assert migrated["events"][:len(legacy_events)] == legacy_events
    assert len(migrated["events"]) > len(legacy_events)
    assert migrated["champions"] == legacy["champions"]
    assert before == legacy


def validate_event_ids(events, registry):
    from act.research.registry import StrategyRegistry

    return StrategyRegistry(events).champions.values()


def test_capture_failure_propagates(tmp_path, monkeypatch):
    def fail(_):
        raise ValueError("observation capture failed")

    monkeypatch.setattr("act.research.dispatch_safety.effective_observation", fail)
    with pytest.raises(ValueError, match="observation capture failed"):
        capture(tmp_path)


def test_environment_change_appends_observation(tmp_path, monkeypatch):
    first = capture(tmp_path)
    save(tmp_path, first)
    monkeypatch.setenv("BINANCE_SPOT_TAKER_RATE", "0.002")
    assert effective_observation(settings())["costs"]["spot"]["taker"]["value"] == 0.002
    changed = capture(tmp_path)
    assert changed["events"][:len(first["events"])] == first["events"]
    assert len(changed["events"]) > len(first["events"])
    assert changed["champions"] == first["champions"]
