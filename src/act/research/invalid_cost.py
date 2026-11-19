"""PAPER ONLY captured invalid-cost veto; no accepts, sizing or dispatch."""
import ast
import math

from act.research.early_risk import UnsupportedPolicy


GUARD = """if not all(math.isfinite(x) and 0 <= x < 100 for x in (fee_pct, slippage_pct)):
    return reject("Invalid fee or slippage assumption")
"""
HELPER = """def reject(reason, risk=0):
    return RiskCheckResult(accepted=False, reason=reason, risk_usd=risk)
"""


def evaluate_invalid_cost(snapshot):
    """Complete RiskCheckResult mapping for invalid captured costs ONLY.

    Pinned captured generator guard and return topology are interpreted explicitly;
    all finite/nonfinite cost invalidity precedes leverage/stop in production.
    """
    try:
        policy = snapshot.policy
        selection = policy["selection"]
        if snapshot.market != "solana" or selection["market"] != "solana":
            raise UnsupportedPolicy("Solana invalid-cost subset only")
        if selection["dispatch"] != "off":
            raise UnsupportedPolicy("Dispatch must remain OFF")
        source = selection["named_production_rules"]["act.risk.limits"][
            "definitions"]["act.risk.limits.check_trade"]
        body = ast.parse(source).body[0].body
        if (ast.dump(body[0]) != ast.dump(ast.parse(HELPER).body[0])
                or ast.dump(body[1]) != ast.dump(ast.parse(GUARD).body[0])):
            raise UnsupportedPolicy("Captured invalid-cost guard/reason drift")
        costs = policy["costs"]["effective"]
        # Read iteration names and numeric guard bounds from the captured AST.
        branch = body[1]
        generator = branch.test.operand.args[0]
        values = [costs[n.id] for n in generator.generators[0].iter.elts]
        lower, upper = (generator.elt.values[1].left.value,
                        generator.elt.values[1].comparators[1].value)
        if any(type(v) not in (int, float) for v in values):
            raise UnsupportedPolicy("Nonnumeric costs")
        if not all(math.isfinite(v) and lower <= v < upper for v in values):
            return {"accepted": False, "reason": branch.body[0].value.args[0].value,
                    "risk_usd": 0.0}
    except (KeyError, TypeError, IndexError, SyntaxError, AttributeError) as exc:
        raise UnsupportedPolicy("CAPTURE_GAP or unsupported invalid-cost capture") from exc
    raise UnsupportedPolicy("Valid-cost continuation NOT implemented; dispatch OFF")
