"""Fail-closed policy snapshot primitives; NOT activated in the cycle runner.

PAPER ONLY. No ledger, network, promotion or execution interfaces. Callers must
capture actual effective configuration, not reconstruct historical defaults.
"""
import json
from dataclasses import dataclass

from act.research.registry import MARKETS, StrategyRegistry, encoded

SCHEMA = "act.policy.v1"
SECTIONS = {"selection", "entry", "exit", "costs"}


def canonical_policy(policy):
    """Own an immutable canonical value; reject incomplete/non-JSON policies."""
    if not isinstance(policy, dict) or set(policy) != SECTIONS:
        raise ValueError("Full selection/entry/exit/costs policy required")
    if any(not isinstance(policy[key], dict) or not policy[key] for key in SECTIONS):
        raise ValueError("Every policy section requires explicit parameters")
    return encoded(policy)


@dataclass(frozen=True)
class PolicySnapshot:
    version_id: str
    market: str
    canonical: str

    @property
    def policy(self):
        """Detached copy: callers cannot mutate the resolved policy."""
        return json.loads(self.canonical)

    @property
    def bytes(self):
        return self.canonical.encode("utf-8")


def register_policy(registry, market, policy, implementation, rationale, at, parent=None):
    """Append through the existing content-addressed registry; never move pointers.

    implementation is an explicit provenance identifier, not a claim that this
    module inspected source or proved equivalence to a production strategy.
    """
    canonical = canonical_policy(policy)
    if not isinstance(implementation, str) or not implementation.strip():
        raise ValueError("Implementation identity required")
    return registry.register(market, {
        "policy_schema": SCHEMA, "policy": json.loads(canonical),
        "implementation": implementation,
    }, rationale, at, parent=parent)


def resolve_policy(state, market, *, expected_policy, implementation):
    """Resolve a persisted champion; reject legacy identities, drift or mismatch.

    Does not upgrade old checkpoints or fall back to defaults. This standalone
    resolver is deliberately not wired into capture_registry or paper decisions.
    Pointer changes and promotion remain unsupported by StrategyRegistry.
    """
    if state.get("paper_only") is not True or market not in MARKETS:
        raise ValueError("PAPER ONLY and known market required")
    registry = StrategyRegistry(state["events"])
    if set(registry.champions) != set(MARKETS):
        raise ValueError("Every market requires a champion")
    if registry.champions != state["champions"]:
        raise ValueError("Persisted champion pointer mismatch")
    version_id = registry.champions[market]
    version = registry.versions[version_id]
    params = version["parameters"]
    if params.get("policy_schema") != SCHEMA:
        raise ValueError("Full policy snapshot unavailable; legacy identity only")
    canonical = canonical_policy(params["policy"])
    if params.get("implementation") != implementation:
        raise ValueError("Implementation drift")
    if canonical != canonical_policy(expected_policy):
        raise ValueError("Policy drift")
    return PolicySnapshot(version_id, market, canonical)
