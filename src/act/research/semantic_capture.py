"""Standalone PAPER ONLY source-backed v1 capture. No runtime dispatch or I/O.

Resolver compatibility is not an independent semantic-completeness proof. Rule
bodies are data, never eval/exec'd. Custom injected configs and cycle-time values
must not be inferred from this resolution-time capture.
"""
import ast
from dataclasses import dataclass
from decimal import Decimal
import importlib
import inspect
import json
import os

from act.agents import budget, spot_response_limit
from act.agents.movers import configured_top_n
from act.binance_data import configured_base
from act.research.dispatch_safety import MODULES, effective_observation, model_source
from act.research.policy import canonical_policy
from act.research.registry import MARKETS, timestamp
from act.security import ConcentrationSettings


SCHEMA = "act.source-backed-capture.v1"
# Capture expressions, not just fingerprints. This includes the rule dependencies
# that parse/validate public evidence before it can enter a decision.
SOURCES = (*MODULES, "act.agents", "act.binance_data", "act.domain.models",
           "act.collectors.base", "act.collectors.geckoterminal",
           "act.collectors.rugcheck", "act.collectors.coingecko")


def rule_inventory():
    """Qualified production definitions, assignments and branch expressions.

    Keeping entire definitions preserves ordering, strict/inclusive boundaries,
    veto text, formulas, defaults and interactions (including override priority).
    An unavailable source is a loud capture failure, never an empty section.
    """
    result = {}
    for name in SOURCES:
        module = importlib.import_module(name)
        tree = ast.parse(inspect.getsource(module))
        definitions, guards, assignments = {}, {}, {}

        def visit(node, path):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                path = path + "." + node.name
                definitions[path] = ast.unparse(node)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                assignments[f"{path}:{node.lineno}"] = ast.unparse(node)
            if isinstance(node, (ast.If, ast.IfExp, ast.Assert, ast.While)):
                guards[f"{path}:{node.lineno}"] = ast.unparse(node.test)
            for child in ast.iter_child_nodes(node):
                visit(child, path)

        visit(tree, name)
        if not definitions:
            raise ValueError("SEMANTIC_CAPTURE_SOURCE_UNAVAILABLE " + name)
        result[name] = {"definitions": definitions, "guards": guards,
                        "assignments": assignments}
    return result


def model_row(cls):
    values = cls().model_dump(mode="json")
    prefix = cls.model_config.get("env_prefix", "")
    return {"values": values, "bounds": cls.model_json_schema()["properties"],
            "sources": {key: {"variable": (prefix + key).upper(),
                              "source": model_source((prefix + key).upper())}
                        for key in values}}


def resolved_controls(market):
    """Use production resolution and bounds; never dump environment contents."""
    controls = {}
    for name, seconds, fetches in (("scanner_" + market, 12, 6),
                                  ("technical_" + market, 12, 6),
                                  ("trade_execution_simulation", 30, 20)):
        for kind, default in (("TIME_BUDGET_SECONDS", seconds), ("FETCH_BUDGET", fetches)):
            variable, fallback = "AGENT_" + name.upper() + "_" + kind, "AGENT_" + kind
            source = variable if variable in os.environ else fallback if fallback in os.environ else "production_default"
            controls[variable] = {"value": budget(name, kind, default), "source": source,
                                  "minimum": 0, "cap": 60 if kind == "TIME_BUDGET_SECONDS" else 20,
                                  "integer": kind == "FETCH_BUDGET"}
    variable = "BINANCE_" + market.upper() + "_BASE_URL"
    controls[variable] = {"value": configured_base(market),
                          "source": variable if variable in os.environ else "production_default",
                          "bounds": "production official-origin allowlist; see act.binance_data"}
    if market == "spot":
        controls["BINANCE_SPOT_MOVER_TOP_N"] = {
            "value": configured_top_n(), "minimum": 0, "maximum": 10,
            "source": "environment" if "BINANCE_SPOT_MOVER_TOP_N" in os.environ else "production_default"}
        for endpoint, variable in (("exchangeInfo", "BINANCE_SPOT_EXCHANGE_INFO_MAX_BYTES"),
                                   ("ticker/24hr", "BINANCE_SPOT_TICKER_MAX_BYTES")):
            controls[variable] = {"value": spot_response_limit("https://api.binance.com/api/v3/" + endpoint),
                                  "minimum": 1, "maximum": 32_000_000,
                                  "source": variable if variable in os.environ else "production_default"}
    return controls


def instrument_capture(market, observation, required_symbols):
    """Own supplied public exchangeInfo, without fetching or inventing minimums.

    Scope is explicit: every requested instrument must be present. Original
    decimal strings, all filters and provenance are retained, including both
    NOTIONAL variants and MARKET_LOT_SIZE. Blocked futures is NOT tradable.
    """
    if market == "solana":
        if observation is not None or required_symbols:
            raise ValueError("Solana has no Binance instrument minimums")
        return {"status": "not_applicable", "reason": "USD-quoted pool paper path; no exchange lot/min-notional gate"}
    if not isinstance(observation, dict):
        raise ValueError("DYNAMIC_INSTRUMENT_MINIMUMS_REQUIRED " + market)
    owned = json.loads(json.dumps(observation, allow_nan=False))
    timestamp(owned["fetched_at"])
    if not isinstance(owned.get("source"), str) or not owned["source"].strip():
        raise ValueError("Public instrument source required")
    if owned.get("status") == "blocked_http_451":
        if market != "futures" or required_symbols or owned.get("symbols") != []:
            raise ValueError("Blocked capture cannot contain or authorize instruments")
        return {**owned, "tradable": False, "required_symbols": []}
    if owned.get("status") != "available" or not required_symbols:
        raise ValueError("Available capture requires an explicit nonempty instrument scope")
    rows = owned["symbols"]
    if not isinstance(rows, list) or len({r["symbol"] for r in rows}) != len(rows):
        raise ValueError("Duplicate/invalid instrument inventory")
    by_symbol = {row["symbol"]: row for row in rows}
    if set(by_symbol) != set(required_symbols) or len(set(required_symbols)) != len(required_symbols):
        raise ValueError("Instrument scope mismatch; missing or extra runtime instruments")
    for row in rows:
        for key in ("status", "quoteAsset", "baseAsset"):
            if not isinstance(row[key], str) or not row[key]:
                raise ValueError("Missing instrument identity " + key)
        filters = {f["filterType"]: f for f in row["filters"]}
        if len(filters) != len(row["filters"]):
            raise ValueError("Duplicate instrument filters")
        for kind in ("LOT_SIZE", "MARKET_LOT_SIZE"):
            if kind == "MARKET_LOT_SIZE" and kind not in filters:
                continue  # Production explicitly allows absence.
            for key in ("minQty", "maxQty", "stepSize"):
                value = Decimal(str(filters[kind][key]))
                if not value.is_finite() or value < 0:
                    raise ValueError("Invalid instrument quantity bound")
        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
        if not notional:
            raise ValueError("Missing dynamic MIN_NOTIONAL/NOTIONAL")
        low = Decimal(str(notional.get("minNotional", notional.get("notional"))))
        high = Decimal(str(notional.get("maxNotional", "0")))
        if not low.is_finite() or low < 0 or not high.is_finite() or high < 0:
            raise ValueError("Invalid dynamic notional bounds")
    return {**owned, "required_symbols": sorted(required_symbols), "tradable": False,
            "scope": "supplied instruments only; not a claimed full exchange universe"}


@dataclass(frozen=True)
class CapturedPolicy:
    market: str
    canonical: str

    @property
    def policy(self):
        return json.loads(self.canonical)


def capture_policy(settings, market, *, at, instruments=None, required_symbols=()):
    """Resolution-time artifact, NOT wired to checkpoints, promotion or decisions.

    Resolver-accepted four-section capture, with explicit semantic-audit limits.
    No assertion of equivalence, authentication, cycle-freezing or activation.
    """
    if settings.paper_only is not True or market not in MARKETS:
        raise ValueError("PAPER ONLY and known market required")
    timestamp(at)
    observation = effective_observation(settings)
    models = observation["models"]
    models["ConcentrationSettings"] = model_row(ConcentrationSettings)
    inventory = rule_inventory()
    dynamic = instrument_capture(market, instruments, required_symbols)
    policy = {
        "selection": {"schema": SCHEMA, "market": market, "captured_at": at,
            "capture_status": "source_backed; independent_semantic_audit_pending",
            "models": models, "controls": {} if market == "solana" else resolved_controls(market),
            "named_production_rules": inventory,
            "data_status": "blocked_http_451" if dynamic["status"] == "blocked_http_451" else "public_data",
            "dispatch": "off", "time_basis": "resolution_time_NOT_cycle_frozen"},
        "entry": {"risk_limits": observation["risk_limits"]["solana" if market == "solana" else "binance"],
                  "instrument_minimums": dynamic,
                  "rules": "act.strategy.evaluate/run_strategy + act.risk.limits.check_trade" if market == "solana" else
                           "act.agents.binance_paper.RiskManagementAgent.approve/round_size/minimums/fill_price",
                  "definition_location": "selection.named_production_rules (full bodies, defaults and guards)"},
        "exit": {"rules": "act.paper.positions.process_positions + act.paper.executor.PaperExecutor.close" if market == "solana" else
                          "act.agents.binance_paper.run_pipeline/TradeExecutionSimulationAgent.close/funding",
                 "definition_location": "selection.named_production_rules; includes TP/SL/trailing/partial priority"},
        "costs": {"effective": observation["costs"][market],
                  "transport": {key: {"value": getattr(settings, key),
                      "bounds": settings.model_json_schema()["properties"][key],
                      "source": "explicit_resolved_Settings_instance; env/dotenv/constructor origin not recoverable"}
                      for key in ("fee_pct", "slippage_pct", "http_timeout", "http_retries")},
                  "fingerprints": observation["implementation"],
                  "limits": ["fingerprints shape-checked, NOT authenticated",
                      "resolver compatibility is NOT proof of complete semantic coverage",
                      "no independent dual-path decision-byte corpus; dispatch OFF",
                      "custom injected configs/limits not represented by default resolution"]},
    }
    return CapturedPolicy(market, canonical_policy(policy))
