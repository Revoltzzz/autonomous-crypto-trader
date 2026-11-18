"""Offline prerequisite evidence, NOT all-market dispatch equivalence."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, SecurityAssessment, TokenDiscovery
from act.persistence import LocalPersistence
from act.research.checkpoint import capture_registry, load_registry
from act.research.dispatch_safety import (
    MODELS, MODULES, decision_bytes, effective_observation, migrate_observation,
    prove_case, require_dispatch_off,
)
from act.research.policy import resolve_policy
from act.research.registry import MARKETS, encoded
from act.strategy import StrategySettings, evaluate

AT = "2026-11-18T00:00:00+00:00"
LATER = "2026-11-18T01:00:00+00:00"
NOW = datetime.fromisoformat(AT)


@pytest.fixture
def observation(monkeypatch):
    for cls in MODELS:
        prefix = cls.model_config.get("env_prefix", "")
        for field in cls.model_fields:
            monkeypatch.delenv((prefix + field).upper(), raising=False)
    for market in ("SPOT", "FUTURES"):
        for kind in ("MAKER", "TAKER"):
            monkeypatch.delenv("BINANCE_" + market + "_" + kind + "_RATE", raising=False)
    return effective_observation(Settings(_env_file=None))


def legacy(tmp_path):
    state = capture_registry({"paper_only": True, "started_at": AT},
                             LocalPersistence(tmp_path), Settings(_env_file=None))
    # Reconstruct the pre-observation identity/cost checkpoint explicitly.
    state["events"] = state["events"][:6]
    for key in ("policy_observations", "policy_migration_status", "dispatch_status"):
        state.pop(key, None)
    return state


def test_capture_known_model_completeness_and_bounds(observation):
    assert observation["complete"] is False
    assert observation["gaps"]
    for cls in MODELS:
        row = observation["models"][cls.__name__]
        assert set(row["values"]) == set(cls.model_fields)
        assert set(row["bounds"]) == set(cls.model_fields)
        assert set(row["sources"]) == set(cls.model_fields)
        assert row["values"] == cls().model_dump(mode="json")
        assert all(v["source"] == "production_default" for v in row["sources"].values())
    assert observation["models"]["StrategySettings"]["bounds"]["max_open_positions"]["maximum"] == 3
    assert observation["risk_limits"]["solana"]["max_drawdown_pct"] == 10
    assert set(observation["implementation"]) == set(MODULES)
    assert all(len(v) == 64 for v in observation["implementation"].values())
    assert observation["markets"]["futures"]["data_status"] == "blocked_http_451"
    assert all(v["dispatch"] == "off" for v in observation["markets"].values())


def test_environment_effective_values_and_sources(observation, monkeypatch):
    monkeypatch.setenv("STRATEGY_MIN_LIQUIDITY_USD", "12000")
    monkeypatch.setenv("BINANCE_SPOT_TAKER_RATE", ".002")
    value = effective_observation(Settings(_env_file=None, fee_pct=.3))
    row = value["models"]["StrategySettings"]
    assert row["values"]["min_liquidity_usd"] == 12000
    assert row["sources"]["min_liquidity_usd"] == {
        "source": "environment", "variable": "STRATEGY_MIN_LIQUIDITY_USD"}
    assert value["costs"]["spot"]["taker"]["value"] == .002
    assert value["costs"]["spot"]["taker"]["source"] == "environment"
    assert value["costs"]["solana"]["fee_pct"] == .3
    assert observation["models"]["StrategySettings"]["values"]["min_liquidity_usd"] == 10000


@pytest.mark.parametrize("name,value", [
    ("STRATEGY_MAX_OPEN_POSITIONS", "4"), ("RISK_MAX_POSITION_NOTIONAL_FRACTION", "2"),
    ("BINANCE_FUTURES_TAKER_RATE", ".011"), ("VOLUME_PROFILE_REQUIRED_FOR_BUY", "false"),
])
def test_invalid_effective_environment_fails(observation, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        effective_observation(Settings(_env_file=None))


def test_no_settings_secrets_or_arbitrary_environment(observation, monkeypatch):
    monkeypatch.setenv("UNRELATED_API_KEY", "fixture-do-not-capture")
    value = effective_observation(Settings(_env_file=None, supabase_service_key="fixture-secret"))
    text = encoded(value)
    assert "fixture-secret" not in text
    assert "fixture-do-not-capture" not in text
    assert "supabase_service_key" not in text
    with pytest.raises(ValueError, match="PAPER ONLY"):
        effective_observation(SimpleNamespace(paper_only=False))


def test_append_only_migration_and_persistence(tmp_path, observation):
    old = legacy(tmp_path)
    before = deepcopy(old)
    new = migrate_observation(old, observation, AT)
    assert old == before
    assert new["events"][:len(old["events"])] == old["events"]
    assert len(new["events"]) == len(old["events"]) + 3
    assert new["champions"] == old["champions"]
    assert set(new["policy_observations"]) == set(MARKETS)
    assert "legacy champions unchanged" in new["policy_migration_status"]
    assert migrate_observation(new, observation, LATER) == new
    store = LocalPersistence(tmp_path)
    store.save({"cycle_id": "fixture", "paper_only": True, "started_at": AT,
                "research_registry": new}, None)
    restored = load_registry(store)
    assert restored == new
    assert migrate_observation(restored, observation, LATER) == new
    restored["events"].clear()
    assert load_registry(store) == new


def test_observed_drift_appends_not_rewrites(tmp_path, observation, monkeypatch):
    old = migrate_observation(legacy(tmp_path), observation, AT)
    monkeypatch.setenv("STRATEGY_TARGET_FRACTION", ".5")
    current = effective_observation(Settings(_env_file=None))
    new = migrate_observation(old, current, LATER)
    assert len(new["events"]) == len(old["events"]) + 3
    assert new["events"][:len(old["events"])] == old["events"]
    assert new["champions"] == old["champions"]
    assert new["policy_observations"] != old["policy_observations"]


@pytest.mark.parametrize("damage", ["champions", "events", "index", "partial_index"])
def test_migration_corruption_fails(tmp_path, observation, damage):
    old = migrate_observation(legacy(tmp_path), observation, AT)
    if damage == "champions":
        old["champions"]["spot"] = "wrong"
    elif damage == "events":
        old["events"][0]["version"]["rationale"] = "tampered"
    elif damage == "index":
        old["policy_observations"]["spot"] = old["champions"]["solana"]
    else:
        del old["policy_observations"]["spot"]
    with pytest.raises(ValueError):
        migrate_observation(old, observation, LATER)


def test_partial_observation_never_passes_full_resolver(tmp_path, observation):
    new = migrate_observation(legacy(tmp_path), observation, AT)
    with pytest.raises(ValueError, match="legacy identity"):
        resolve_policy(new, "solana", expected_policy={}, implementation="v1")
    for bad in ({}, {**observation, "complete": True}, {**observation, "paper_only": False}):
        with pytest.raises(ValueError):
            migrate_observation(new, bad, LATER)


@pytest.mark.parametrize("flag", [None, "false", "FALSE"])
def test_reserved_kill_switch_off(monkeypatch, flag):
    monkeypatch.delenv("ACT_POLICY_DISPATCH", raising=False)
    if flag is not None:
        monkeypatch.setenv("ACT_POLICY_DISPATCH", flag)
    assert require_dispatch_off() == "hardcoded_v1"


@pytest.mark.parametrize("flag", ["true", "TRUE", "1", "maybe"])
def test_activation_unconditionally_blocked(monkeypatch, flag):
    monkeypatch.setenv("ACT_POLICY_DISPATCH", flag)
    with pytest.raises(ValueError):
        require_dispatch_off()


def test_byte_comparator_has_no_masking_or_rounding():
    value = {"decision": "BUY", "size": 1., "id": "fixed", "veto": []}
    assert prove_case("fixture", value, deepcopy(value))["scope"] == "single_fixture_only"
    for key, replacement in (("decision", "REJECT"), ("size", 1.0000000000000002),
                             ("id", "different"), ("veto", ["new veto"])):
        changed = {**value, key: replacement}
        with pytest.raises(ValueError, match="DECISION_BYTE_DIVERGENCE fixture"):
            prove_case("fixture", value, changed)
    with pytest.raises(ValueError):
        decision_bytes({"size": float("nan")})
    assert json.loads(decision_bytes(value)) == value


CASES = [
    ("accept_boundaries", {}, {}, {}, "BUY"),
    ("score_boundary", {}, {"score": 500}, {}, "REJECT"),
    ("lp_below", {}, {"lp_locked_pct": 79.99}, {}, "REJECT"),
    ("mint_authority", {}, {"mint_authority_revoked": False}, {}, "REJECT"),
    ("freeze_authority", {}, {"freeze_authority_revoked": False}, {}, "REJECT"),
    ("holder_boundary", {}, {"top_holder_concentration_pct": 50}, {}, "REJECT"),
    ("security_missing", {}, {"score": None}, {}, "REJECT"),
    ("liquidity_below", {"liquidity_usd": 9999.99}, {}, {}, "REJECT"),
    ("volume_below", {"volume_24h_usd": 24999.99}, {}, {}, "REJECT"),
    ("ratio_below", {"buys_24h": 109}, {}, {}, "REJECT"),
    ("zero_sells", {"sells_24h": 0}, {}, {}, "REJECT"),
    ("price_missing", {"price_usd": None}, {}, {}, "REJECT"),
    ("age_boundary", {"first_seen_at": NOW - timedelta(hours=72)}, {}, {}, "BUY"),
    ("age_old", {"first_seen_at": NOW - timedelta(hours=72, seconds=1)}, {}, {}, "REJECT"),
    ("future_age", {"first_seen_at": NOW + timedelta(seconds=1)}, {}, {}, "REJECT"),
    ("preflight_watch", {}, {}, {"screening_only": True}, "WATCH"),
    ("no_profile", {}, {}, {"volume_profile": None}, "REJECT"),
    ("below_val", {"price_usd": 1.6}, {}, {}, "REJECT"),
    ("inside_va", {"price_usd": 1.8}, {}, {}, "REJECT"),
    ("no_account", {}, {}, {"account": None}, "REJECT"),
]


@pytest.mark.parametrize("name,pool_updates,security_updates,options,expected", CASES,
                         ids=[r[0] for r in CASES])
def test_solana_effective_config_roundtrip_bytes(
        observation, monkeypatch, name, pool_updates, security_updates, options, expected):
    """Limited config roundtrip only: NOT a distinct dispatched implementation."""
    monkeypatch.setattr("act.strategy.uuid4", lambda: "fixture-deterministic-id")
    pool = TokenDiscovery(**{**dict(symbol="FIXTURE", mint_address="mint", pool_address="pool",
        first_seen_at=NOW - timedelta(hours=1), price_usd=2, liquidity_usd=10000,
        volume_24h_usd=25000, buys_24h=110, sells_24h=100,
        source_url="https://fixture.invalid/pool"), **pool_updates})
    security = SecurityAssessment(**{**dict(score=1, lp_locked_pct=80,
        mint_authority_revoked=True, freeze_authority_revoked=True,
        top_holder_concentration_pct=12, source="offline fixture"), **security_updates})
    args = {"cycle_id": "fixture", "at": NOW,
        "account": AccountState(equity=10000, cash=10000, day_start_equity=10000,
                                peak_equity=10000),
        "volume_profile": {"status": "available", "poc": 1.8, "val": 1.7, "vah": 1.9,
            "value_area_pct": 70, "actual_value_area_pct": 75,
            "candle_count": 12, "timeframe": "5m"}, **options}
    original = evaluate(pool, security, **deepcopy(args))
    config = StrategySettings(**observation["models"]["StrategySettings"]["values"])
    restored = evaluate(pool, security, config=config, **deepcopy(args))
    assert original.decision == restored.decision == expected
    assert prove_case(name, original, restored)["bytes"] > 0


def test_lowercase_environment_provenance(observation, monkeypatch):
    monkeypatch.setenv("strategy_min_liquidity_usd", "12345")
    row = effective_observation(Settings(_env_file=None))["models"]["StrategySettings"]
    assert row["values"]["min_liquidity_usd"] == 12345
    assert row["sources"]["min_liquidity_usd"]["source"] == "environment"


@pytest.mark.parametrize("damage", ["market", "model", "field", "nonfinite"])
def test_malformed_observation_rejected(tmp_path, observation, damage):
    state = legacy(tmp_path)
    before = deepcopy(state)
    value = deepcopy(observation)
    if damage == "market":
        value["markets"].pop("spot")
    elif damage == "model":
        value["models"].pop("StrategySettings")
    elif damage == "field":
        value["models"]["StrategySettings"]["sources"].pop("min_liquidity_usd")
    else:
        value["costs"]["solana"]["fee_pct"] = float("nan")
    with pytest.raises(ValueError):
        migrate_observation(state, value, at=LATER)
    assert state == before
