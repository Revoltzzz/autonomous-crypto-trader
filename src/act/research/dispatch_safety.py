"""PAPER ONLY dispatch prerequisites, deliberately NOT wired into runtime.

Observations are incomplete and cannot be resolved as act.policy.v1 snapshots.
Never use an observation or a caller-supplied proof to authorize activation.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib
import inspect
import os

from act.agents.binance_paper import RiskLimits as BinanceRiskLimits, fee_rate
from act.collectors.solana_rpc import SecuritySettings
from act.discovery import DiscoverySettings
from act.paper.positions import PositionSettings
from act.research.checkpoint import validate
from act.research.registry import MARKETS, encoded, timestamp
from act.risk.limits import RiskLimits, RiskSettings
from act.strategy import StrategySettings
from act.volume_profile import VolumeProfileSettings

SCHEMA = "act.effective-observation.v1"
MODELS = (StrategySettings, RiskSettings, SecuritySettings, DiscoverySettings,
          PositionSettings, VolumeProfileSettings)
MODULES = ("act.strategy", "act.risk.limits", "act.paper.executor",
           "act.paper.ledger", "act.paper.positions", "act.volume_profile",
           "act.discovery", "act.security", "act.collectors.solana_rpc",
           "act.agents.scanner", "act.agents.movers", "act.agents.binance_paper")
GAPS = ("Named hardcoded selection/entry/exit and TA/fill constants not fully modeled",
        "Exchange minimums are per-instrument public runtime inputs, not captured here",
        "No all-veto/boundary decision-byte corpus for all three markets",
        "Resolution-time environment observation is not a cycle-frozen configuration")


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def effective_observation(settings):
    """Resolve allowlisted production models and fee functions, without HTTP.

    Captures effective values, field bounds and environment variable names, never
    all of os.environ or Settings (which contains persistence credentials).
    Independent resolutions: NOT evidence of decision-time config equivalence.
    """
    if settings.paper_only is not True:
        raise ValueError("PAPER ONLY required")
    models = {}
    for cls in MODELS:
        instance = cls()
        prefix = cls.model_config.get("env_prefix", "")
        values = instance.model_dump(mode="json")
        properties = cls.model_json_schema()["properties"]
        models[cls.__name__] = {
            "values": values, "bounds": properties,
            "sources": {name: {"variable": (prefix + name).upper(),
                "source": "environment" if (prefix + name).upper() in os.environ
                else "production_default"} for name in values}}
    fees = {}
    for market in ("spot", "futures"):
        fees[market] = {}
        for maker in (False, True):
            name = "maker" if maker else "taker"
            variable = "BINANCE_" + market.upper() + "_" + name.upper() + "_RATE"
            fees[market][name] = {"value": fee_rate(market, maker),
                "variable": variable, "source": "environment" if variable in os.environ
                else "production_default", "bounds": {"minimum": 0, "maximum": .01}}
    return {"schema": SCHEMA, "paper_only": True, "complete": False,
        "models": models, "risk_limits": {"solana": asdict(RiskLimits()),
                                           "binance": asdict(BinanceRiskLimits())},
        "costs": {"solana": {"fee_pct": settings.fee_pct,
            "slippage_pct": settings.slippage_pct, "source": "resolved_Settings_instance"},
            **fees},
        "implementation": {name: hashlib.sha256(
            inspect.getsource(importlib.import_module(name)).encode()).hexdigest()
            for name in MODULES},
        "markets": {market: {"strategy": "v1", "dispatch": "off",
            "data_status": "blocked_http_451" if market == "futures" else "public_data"}
            for market in MARKETS}, "gaps": list(GAPS)}


def migrate_observation(state, observation, at):
    """Append observation child versions; never reinterpret legacy baselines.

    Pure value transform. Persist returned state through existing cycle storage
    explicitly. Not called by capture_registry; runtime migration stays OFF.
    Historical snapshots are unknown, not reconstructed from today's defaults.
    """
    timestamp(at)
    registry = validate(state)
    if (observation.get("schema") != SCHEMA or observation.get("paper_only") is not True
            or observation.get("complete") is not False):
        raise ValueError("Incomplete PAPER observation schema required")
    result = deepcopy(state)
    previous = result.get("policy_observations", {})
    if previous and set(previous) != set(MARKETS):
        raise ValueError("Observation index requires all markets")
    pointers = {}
    for market in MARKETS:
        champion = registry.champions[market]
        parameters = {"observation_schema": SCHEMA,
            "configuration_status": "partial_capture_NOT_dispatchable",
            "baseline_version_id": champion, "observation": observation}
        old_id = previous.get(market)
        if old_id:
            old = registry.versions.get(old_id)
            if (not old or old["market"] != market or old["parent"] != champion
                    or old["parameters"].get("baseline_version_id") != champion
                    or old["parameters"].get("observation_schema") != SCHEMA):
                raise ValueError("Observation index/pointer mismatch")
            if old["parameters"] == parameters:
                pointers[market] = old_id
                continue
        pointers[market] = registry.register(market, parameters,
            "Legacy v1 retained; resolution-time partial observation only; dispatch OFF",
            at, parent=champion)
    result.update(events=registry.events, policy_observations=pointers,
        policy_migration_status="partial_observation_only; legacy champions unchanged",
        dispatch_status="off; full capture and all-market equivalence incomplete")
    validate(result)
    return result


def decision_bytes(value):
    """Exact canonical decision bytes; no field stripping or float rounding."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return encoded(value).encode("utf-8")


def prove_case(name, hardcoded, proposed):
    """Compare actual outputs; mismatch always raises with hashes, never passes.

    Fixtures must pin nondeterministic IDs/time in both runs, not remove fields.
    One passing case is not a complete corpus nor an activation credential.
    """
    left, right = decision_bytes(hardcoded), decision_bytes(proposed)
    if left != right:
        raise ValueError("DECISION_BYTE_DIVERGENCE " + name + " hardcoded="
            + hashlib.sha256(left).hexdigest() + " proposed="
            + hashlib.sha256(right).hexdigest())
    return {"case": name, "sha256": hashlib.sha256(left).hexdigest(),
            "bytes": len(left), "scope": "single_fixture_only"}


def require_dispatch_off():
    """Reserved kill switch; true cannot activate an unproven implementation.

    Runtime is still hardcoded regardless: this guard is a prerequisite helper,
    not a production switch. There is intentionally no activation bypass.
    """
    value = os.getenv("ACT_POLICY_DISPATCH", "false").lower()
    if value not in ("false", "true"):
        raise ValueError("ACT_POLICY_DISPATCH must be true or false")
    if value == "true":
        raise ValueError("DISPATCH_BLOCKED: complete capture/equivalence unavailable")
    return "hardcoded_v1"
