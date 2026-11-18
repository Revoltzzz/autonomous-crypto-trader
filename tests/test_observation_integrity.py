"""Partial observation integrity only; NOT independent dispatch equivalence."""
from copy import deepcopy

import pytest

from act.config.settings import Settings
from act.persistence import LocalPersistence
from act.research.checkpoint import capture_registry
from act.research.dispatch_safety import MODELS, effective_observation, migrate_observation

AT = "2026-11-18T00:00:00+00:00"
LATER = "2026-11-18T01:00:00+00:00"


@pytest.fixture
def captured(tmp_path, monkeypatch):
    for cls in MODELS:
        prefix = cls.model_config.get("env_prefix", "")
        for name in cls.model_fields:
            monkeypatch.delenv((prefix + name).upper(), raising=False)
    for market in ("SPOT", "FUTURES"):
        for name in ("MAKER", "TAKER"):
            monkeypatch.delenv("BINANCE_" + market + "_" + name + "_RATE", raising=False)
    settings = Settings(_env_file=None)
    report = {"paper_only": True, "started_at": AT}
    state = capture_registry(report, LocalPersistence(tmp_path), settings)
    return state, effective_observation(settings)


DAMAGES = [
    (("models", "StrategySettings", "values", "max_open_positions"), 4),
    (("models", "StrategySettings", "values", "min_liquidity_usd"), "10000"),
    (("models", "StrategySettings", "bounds", "max_open_positions"), {}),
    (("models", "StrategySettings", "sources", "min_liquidity_usd", "source"), "invented"),
    (("models", "StrategySettings", "sources", "min_liquidity_usd", "variable"), "OTHER"),
    (("models", "StrategySettings", "extra"), True),
    (("risk_limits", "solana"), {}),
    (("risk_limits", "solana", "max_drawdown_pct"), -1),
    (("risk_limits", "binance", "max_positions"), 0),
    (("risk_limits", "extra"), {}),
    (("costs", "solana", "fee_pct"), -1),
    (("costs", "solana", "slippage_pct"), 100),
    (("costs", "solana", "fee_pct"), True),
    (("costs", "solana", "source"), "invented"),
    (("costs", "spot", "maker", "value"), .010001),
    (("costs", "futures", "taker", "value"), -1),
    (("costs", "spot", "maker", "value"), "0.001"),
    (("costs", "spot", "maker", "value"), True),
    (("costs", "futures", "taker", "bounds"), {}),
    (("costs", "spot", "maker", "source"), "invented"),
    (("costs", "spot", "maker", "variable"), "OTHER"),
    (("costs", "spot", "extra"), {}),
    (("costs", "extra"), {}),
    (("implementation", "act.strategy"), "g" * 64),
    (("implementation", "act.strategy"), "a" * 63),
    (("implementation", "act.strategy"), None),
    (("implementation", "extra"), "a" * 64),
    (("markets", "futures", "data_status"), "public_data"),
    (("markets", "spot", "dispatch"), "on"),
    (("markets", "solana", "strategy"), "v2"),
    (("gaps",), []),
]


@pytest.mark.parametrize("path,replacement", DAMAGES)
def test_corrupt_contents_fail_before_append(captured, path, replacement):
    state, observation = captured
    before = deepcopy(state)
    row = observation
    for key in path[:-1]:
        row = row[key]
    row[path[-1]] = replacement
    with pytest.raises(ValueError):
        migrate_observation(state, observation, LATER)
    assert state == before


def test_valid_nondefault_observation_remains_append_only(captured, monkeypatch):
    state, _ = captured
    before = deepcopy(state)
    monkeypatch.setenv("STRATEGY_MIN_LIQUIDITY_USD", "12345")
    monkeypatch.setenv("BINANCE_SPOT_TAKER_RATE", ".002")
    observation = effective_observation(Settings(_env_file=None))
    new = migrate_observation(state, observation, LATER)
    assert state == before
    assert new["events"][:len(state["events"])] == state["events"]
    assert len(new["events"]) == len(state["events"]) + 3
    assert new["champions"] == state["champions"]
    assert migrate_observation(new, observation, LATER) == new
    assert new["dispatch_status"].startswith("off;")


def test_checkpoint_capture_propagates_content_failure(tmp_path, captured, monkeypatch):
    _, observation = captured
    observation["markets"]["futures"]["data_status"] = "public_data"
    monkeypatch.setattr("act.research.dispatch_safety.effective_observation",
                        lambda settings: observation)
    store = LocalPersistence(tmp_path)
    with pytest.raises(ValueError, match="market status"):
        capture_registry({"paper_only": True, "started_at": LATER}, store,
                         Settings(_env_file=None))
    assert not list(tmp_path.glob("cycles/*.json"))
