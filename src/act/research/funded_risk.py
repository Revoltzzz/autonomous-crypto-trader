"""PAPER ONLY: bounded captured Solana take-profit/cash risk interpreter."""
import ast
import operator

from act.research.early_risk import UnsupportedPolicy, evaluate_early_risk


OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
       ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.LtE: operator.le, ast.Gt: operator.gt, ast.IsNot: operator.is_not}
PREFIX = (
    "entry = proposal.entry_price * (1 + slippage_pct / 100)",
    "proposal.take_profit is not None and proposal.take_profit <= entry",
    "exit_at_stop = proposal.stop_loss * (1 - slippage_pct / 100)",
    "cost = entry * proposal.size * (1 + fee_pct / 100)",
    "proceeds = exit_at_stop * proposal.size * (1 - fee_pct / 100)",
    "risk = cost - proceeds",
    "cost > account.cash",
)


def _read(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in {"proposal", "account"}):
        return env[node.value.id][node.attr]
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_read(node.left, env), _read(node.right, env))
    if (isinstance(node, ast.Compare) and len(node.ops) == 1
            and type(node.ops[0]) in OPS):
        return OPS[type(node.ops[0])](_read(node.left, env),
                                     _read(node.comparators[0], env))
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return all(_read(value, env) for value in node.values)
    raise UnsupportedPolicy("Unsupported funded-risk operand")


def evaluate_funded_risk(snapshot, proposal, account):
    """Complete RiskCheckResult for early/TP/cash rejects, never an accept.

    Reads captured formulas, branch ordering, costs and reason strings. Account
    and proposal are caller-supplied synthetic inputs, not frozen cycle state.
    No production evaluator calls, eval/exec, sizing or dispatch activation.
    """
    try:
        return evaluate_early_risk(snapshot, proposal)
    except UnsupportedPolicy as exc:
        if str(exc) != "Remainder of Solana risk/strategy NOT implemented; dispatch OFF":
            raise
    policy = snapshot.policy
    source = policy["selection"]["named_production_rules"]["act.risk.limits"][
        "definitions"]["act.risk.limits.check_trade"]
    body = ast.parse(source).body[0].body
    # Exact prefix topology: nested reject helper, cost guard, three early guards.
    statements = body[5:12]
    shapes = tuple(ast.unparse(n.test if isinstance(n, ast.If) else n)
                   for n in statements)
    if shapes != PREFIX:
        raise UnsupportedPolicy("Captured funded-risk guard/formula drift")
    env = {"proposal": proposal, "account": account,
           **{k: policy["costs"]["effective"][k]
              for k in ("fee_pct", "slippage_pct")}}
    for node in statements:
        if isinstance(node, ast.Assign):
            env[node.targets[0].id] = _read(node.value, env)
        elif _read(node.test, env):
            if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
                raise UnsupportedPolicy("Unsupported funded-risk veto body")
            call = node.body[0].value
            expected_args = 1 if "risk" not in env else 2
            if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
                    or call.func.id != "reject" or call.keywords
                    or len(call.args) != expected_args
                    or not isinstance(call.args[0], ast.Constant)
                    or not isinstance(call.args[0].value, str)
                    or (expected_args == 2 and ast.unparse(call.args[1]) != "risk")):
                raise UnsupportedPolicy("Unsupported funded-risk veto result")
            return {"accepted": False, "reason": call.args[0].value,
                    "risk_usd": env.get("risk", 0.0)}
    raise UnsupportedPolicy("Later risk/sizing/acceptance NOT implemented; dispatch OFF")
