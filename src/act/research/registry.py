"""Append-only strategy research registry. No execution interfaces."""
import hashlib
import json
from datetime import datetime, timezone

MARKETS = ("solana", "spot", "futures")
BASELINE = "bc1376880417592c82fbde6eff2c6e1606bb162a"


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


class StrategyRegistry:
    """Single-writer event value; exports are copies, never mutable internal state.

    Persist history in the existing cycle JSON. No mutation/deletion API.
    Champion pointers are derived from explicit events, not version overwrites.
    """
    def __init__(self, events=()):
        self._events = ()
        for event in events:
            self._accept(json.loads(encoded(event)))

    @property
    def events(self):
        return [json.loads(row) for row in self._events]

    @property
    def versions(self):
        return {e["version"]["id"]: e["version"] for e in self.events if e["kind"] == "version"}

    @property
    def champions(self):
        return {e["market"]: e["version_id"] for e in self.events if e["kind"] == "baseline"}

    def _accept(self, event):
        kind = event["kind"]
        if kind == "version":
            v = event["version"]
            timestamp(v["created_at"])
            if v["market"] not in MARKETS or not v["rationale"].strip():
                raise ValueError("Market/rationale required")
            identity = {k: v[k] for k in ("market", "parameters", "rationale", "created_at", "parent")}
            if v["id"] != hashlib.sha256(encoded(identity).encode()).hexdigest():
                raise ValueError("Version identity mismatch")
            if v["id"] in self.versions:
                raise ValueError("Duplicate immutable version")
            if v["parent"] is not None:
                parent = self.versions.get(v["parent"])
                if not parent or parent["market"] != v["market"]:
                    raise ValueError("Parent must exist in same market")
        elif kind == "baseline":
            v = self.versions[event["version_id"]]
            if v["market"] != event["market"] or event["market"] in self.champions:
                raise ValueError("Champion never overwritten")
        else:
            raise ValueError("Unknown registry event; promotions disabled in foundation release")
        self._events += (encoded(event),)

    def register(self, market, parameters, rationale, created_at, parent=None):
        v = dict(market=market, parameters=parameters, rationale=rationale,
                 created_at=created_at, parent=parent)
        v["id"] = hashlib.sha256(encoded(v).encode()).hexdigest()
        self._accept({"kind": "version", "version": v})
        return v["id"]

    def baseline(self, version_id):
        self._accept({"kind": "baseline", "market": self.versions[version_id]["market"],
                      "version_id": version_id})


def baseline_registry():
    """Implementation identities, NOT reconstructed historical parameter settings.

    No old trade is attributed to these IDs. Deployment configuration snapshots
    and activation timestamps must be captured before prospective attribution.
    """
    registry = StrategyRegistry()
    for market in MARKETS:
        version = registry.register(market, {"implementation_commit": BASELINE,
            "configuration_status": "not_captured; historical attribution unavailable"},
            "Existing v1 " + ("Solana candidate strategy" if market == "solana" else
                              "Binance selection/decision pipeline") + "; identity only, no behavior change",
            "2026-11-17T00:00:00+00:00")
        registry.baseline(version)
    return registry
