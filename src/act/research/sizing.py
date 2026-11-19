"""PAPER ONLY sizing-region interpreter. No runtime integration or decisions."""
import ast
import math
import operator

from act.research.early_risk import UnsupportedPolicy


REGION = """risk_budget = account.equity * 0.01
nominal_notional = risk_budget / ((entry - stop) / entry)
entry_cost = entry * (1 + slippage) * (1 + fee)
stop_proceeds = stop * (1 - slippage) * (1 - fee)
notional_cap = account.equity * risk_cfg.max_position_notional_fraction
size = min(nominal_notional / entry, risk_budget / (entry_cost - stop_proceeds),
           notional_cap / slipped_entry) * (1 - 1e-10)
"""
FIELDS = ("risk_budget", "nominal_notional", "entry_cost", "stop_proceeds",
          "notional_cap", "size")
OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
       ast.Mult: operator.mul, ast.Div: operator.truediv}


def _value(node, env):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in {"account", "risk_cfg"}):
        return env[node.value.id][node.attr]
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_value(node.left, env), _value(node.right, env))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "min" and not node.keywords):
        return min(_value(arg, env) for arg in node.args)
    raise UnsupportedPolicy("Unsupported sizing operand")


def evaluate_sizing(snapshot, proposal, account):
    """Complete six-field region mapping, NOT a full strategy decision.

    Entry/stop are upstream caller-supplied proposal state. Stop construction,
    screening, risk acceptance and post-gate reporting are outside this subset.
    Captured AST arithmetic only: no production decisions, eval or exec.
    """
    try:
        policy = snapshot.policy
        selection = policy["selection"]
        if snapshot.market != "solana" or selection["market"] != "solana":
            raise UnsupportedPolicy("Solana sizing only")
        if selection["dispatch"] != "off":
            raise UnsupportedPolicy("Dispatch must remain OFF")
        if policy["entry"]["instrument_minimums"] != {
            "status": "not_applicable",
            "reason": "USD-quoted pool paper path; no exchange lot/min-notional gate",
        }:
            raise UnsupportedPolicy("Unsupported Solana minimums capture")
        source = selection["named_production_rules"]["act.strategy"][
            "definitions"]["act.strategy.evaluate"]
        tree = ast.parse(source)
        slipped = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and ast.unparse(n.targets[0]) == "slipped_entry"]
        if len(slipped) != 1 or ast.unparse(slipped[0]) != (
                "slipped_entry = entry * (1 + slippage)"):
            raise UnsupportedPolicy("Captured slipped-entry drift")
        regions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                for i, stmt in enumerate(node.body):
                    if (isinstance(stmt, ast.Assign)
                            and ast.unparse(stmt.targets[0]) == "risk_budget"):
                        following = node.body[i + len(FIELDS)]
                        if (not isinstance(following, ast.Assign)
                                or ast.unparse(following.targets[0]) != "trade"
                                or not isinstance(following.value, ast.Call)
                                or ast.unparse(following.value.func) != "TradeProposal"
                                or [ast.unparse(k.value) for k in following.value.keywords
                                    if k.arg == "size"] != ["size"]):
                            raise UnsupportedPolicy("Captured sizing boundary/rounding drift")
                        regions.append(node.body[i:i + len(FIELDS)])
        if len(regions) != 1:
            raise UnsupportedPolicy("CAPTURE_GAP sizing region missing/ambiguous")
        statements = regions[0]
        if ast.dump(ast.Module(body=statements, type_ignores=[])) != ast.dump(
                ast.parse(REGION)):
            raise UnsupportedPolicy("Captured sizing formula/topology drift")
        fraction = selection["models"]["RiskSettings"]["values"][
            "max_position_notional_fraction"]
        costs = policy["costs"]["effective"]
        fee, slippage = (costs[k] / 100 for k in ("fee_pct", "slippage_pct"))
        entry, stop, equity = (proposal["entry_price"], proposal["stop_loss"],
                               account["equity"])
        if not all(type(v) in (int, float) and math.isfinite(v)
                   for v in (entry, stop, equity, fraction, fee, slippage)):
            raise UnsupportedPolicy("Nonfinite or nonnumeric sizing input")
        if not (0 < stop < entry and equity > 0 and 0 < fraction <= 1
                and 0 <= fee < 1 and 0 <= slippage < 1):
            raise UnsupportedPolicy("Unsupported sizing input domain")
        env = {"entry": entry, "stop": stop, "fee": fee, "slippage": slippage,
               "account": {"equity": equity},
               "risk_cfg": {"max_position_notional_fraction": fraction}}
        env["slipped_entry"] = _value(slipped[0].value, env)
        for node in statements:
            env[node.targets[0].id] = _value(node.value, env)
        result = {name: env[name] for name in FIELDS}
        if not all(math.isfinite(v) and v > 0 for v in result.values()):
            raise UnsupportedPolicy("Unsupported zero/overflow sizing result")
        return result
    except (KeyError, TypeError, IndexError, SyntaxError, ZeroDivisionError,
            OverflowError) as exc:
        raise UnsupportedPolicy("CAPTURE_GAP or unsupported sizing input") from exc
