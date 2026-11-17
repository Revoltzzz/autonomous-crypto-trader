"""Durable, single-writer research identities in the existing cycle transaction.

This is registration, NOT strategy dispatch. No execution or ledger interfaces.
"""
import json
from copy import deepcopy

from act.research.registry import MARKETS, StrategyRegistry, timestamp

IMPLEMENTATION = "ad2570c"
SCHEMA = 1


def load_registry(store):
    """No fallback on remote errors or malformed state. Missing is first activation."""
    if hasattr(store, "_request"):
        rows = store._request("GET", "cycles", params={
            "select": "report->research_registry", "order": "completed_at.desc",
            "limit": "1", "report->research_registry": "not.is.null"}).json()
        if not isinstance(rows, list) or len(rows) > 1:
            raise ValueError("Invalid registry checkpoint response")
        if not rows:
            return None
        state = rows[0]["research_registry"]
    else:
        paths = sorted(store.directory.glob("*.json"),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
        state = None
        for path in paths:
            report = json.loads(path.read_text(encoding="utf-8"))
            if "research_registry" in report:
                state = report["research_registry"]
                break
        if state is None:
            return None
    validate(state)
    return deepcopy(state)


def validate(state):
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        raise ValueError("Unsupported registry checkpoint")
    timestamp(state["activated_at"])
    registry = StrategyRegistry(state["events"])
    if set(registry.champions) != set(MARKETS):
        raise ValueError("Every market requires a champion")
    if registry.champions != state["champions"]:
        raise ValueError("Registry champion snapshot mismatch")
    if state.get("paper_only") is not True:
        raise ValueError("PAPER ONLY registry required")
    return registry


def capture_registry(report, store, settings):
    """Called after unchanged paper decisions; saved atomically with that cycle.

    IDs survive restarts; settings changes never silently rewrite a version.
    Only explicit non-secret cost settings are captured. Strategy environment
    snapshots and version dispatch remain unavailable, so attribution is disabled.
    """
    if report.get("paper_only") is not True:
        raise ValueError("PAPER ONLY required")
    state = load_registry(store)
    costs = {"fee_pct": settings.fee_pct, "slippage_pct": settings.slippage_pct}
    if state is None:
        at = report["started_at"]
        timestamp(at)
        registry = StrategyRegistry()
        for market in MARKETS:
            identity = registry.register(market, {
                "implementation_commit": IMPLEMENTATION,
                "paper_cost_settings": costs,
                "configuration_status": "identity_and_costs_only; policy_snapshot_pending",
                "data_status": "blocked_http_451" if market == "futures" else "public_data",
            }, "Existing " + market + " v1; registration only; no behavior change", at)
            registry.baseline(identity)
        state = {"schema": SCHEMA, "paper_only": True, "activated_at": at,
                 "events": registry.events, "champions": registry.champions,
                 "registration_cost_settings": costs}
    registry = validate(state)
    state["observed_at"] = report["started_at"]
    state["current_cost_settings"] = costs
    state["configuration_drift"] = costs != state["registration_cost_settings"]
    state["attribution_status"] = "disabled_until_full_policy_snapshot_and_dispatch"
    state["promotion_status"] = "disabled; no actuator in this incremental release"
    state["champions"] = registry.champions
    return deepcopy(state)
