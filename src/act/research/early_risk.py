"""PAPER ONLY partial snapshot interpreter: Solana early risk vetoes only.

Not a strategy evaluator, dispatcher, or complete decision-equivalence proof.
Unsupported stages fail loudly; never turn incomplete evaluation into acceptance.
"""
import ast
import math


class UnsupportedPolicy(ValueError):
    """The snapshot or fixture is outside the explicitly proven subset."""


def _value(node, proposal):
    if isinstance(node, ast.Constant):
        return node.value
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "proposal"
            and node.attr in {"leverage", "stop_loss", "entry_price"}):
        return proposal[node.attr]
    raise UnsupportedPolicy("Unsupported risk operand")


def evaluate_early_risk(snapshot, proposal):
    """Interpret three ordered captured guards and their captured veto strings.

    Input is a CapturedPolicy plus plain proposal data, NOT production configs.
    Output is the COMPLETE RiskCheckResult mapping for early rejects only.
    Does not import/call production decision functions or eval/exec source.
    """
    if snapshot.market != "solana":
        raise UnsupportedPolicy("Only Solana early-risk subset supported; dispatch OFF")
    policy = snapshot.policy
    selection = policy["selection"]
    if selection["dispatch"] != "off":
        raise UnsupportedPolicy("Dispatch must remain OFF")
    costs = policy["costs"]["effective"]
    if not all(math.isfinite(costs[k]) and 0 <= costs[k] < 100
               for k in ("fee_pct", "slippage_pct")):
        raise UnsupportedPolicy("Invalid costs outside this corpus")
    source = selection["named_production_rules"]["act.risk.limits"][
        "definitions"]["act.risk.limits.check_trade"]
    function = ast.parse(source).body[0]
    branches = [node for node in function.body if isinstance(node, ast.If)]
    # Fail closed on ordering/operand/operator drift, not just on unknown nodes.
    expected = ("proposal.leverage != 1", "proposal.stop_loss is None",
                "proposal.stop_loss >= proposal.entry_price")
    if tuple(ast.unparse(b.test) for b in branches[1:4]) != expected:
        raise UnsupportedPolicy("Captured early-risk guard drift")
    for branch in branches[1:4]:
        test = branch.test
        left, right = _value(test.left, proposal), _value(test.comparators[0], proposal)
        op = test.ops[0]
        if isinstance(op, ast.NotEq):
            rejected = left != right
        elif isinstance(op, ast.Is):
            rejected = left is right
        elif isinstance(op, ast.GtE):
            rejected = left >= right
        else:
            raise UnsupportedPolicy("Unsupported comparison")
        if rejected:
            if len(branch.body) != 1 or not isinstance(branch.body[0], ast.Return):
                raise UnsupportedPolicy("Unsupported veto body")
            call = branch.body[0].value
            if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
                    or call.func.id != "reject" or len(call.args) != 1 or call.keywords
                    or not isinstance(call.args[0], ast.Constant)
                    or not isinstance(call.args[0].value, str)):
                raise UnsupportedPolicy("Unsupported veto result")
            return {"accepted": False, "reason": call.args[0].value, "risk_usd": 0.0}
    raise UnsupportedPolicy("Remainder of Solana risk/strategy NOT implemented; dispatch OFF")
