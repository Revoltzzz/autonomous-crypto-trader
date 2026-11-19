"""PAPER ONLY bounded Solana risk-tail interpreter. No runtime integration."""
import ast
import math

from act.research.early_risk import UnsupportedPolicy
from act.research.funded_risk import PREFIX, _read, evaluate_funded_risk


TAIL = "if risk > account.equity * limits.max_risk_per_trade_pct / 100:\n    return reject(f\"Trade risk exceeds {limits.max_risk_per_trade_pct:g}% limit\", risk)\ndaily_loss = max(0, account.day_start_equity - account.equity,\n                 -account.daily_realized_pnl)\ndaily_cap = account.day_start_equity * limits.max_daily_loss_pct / 100\nexposure = account.open_risk_usd + risk\nif daily_loss >= daily_cap or daily_loss + exposure > daily_cap:\n    return reject(f\"Daily loss or projected stop exposure exceeds {limits.max_daily_loss_pct:g}% limit\", risk)\ndrawdown = max(0, account.peak_equity - account.equity)\ndrawdown_cap = account.peak_equity * limits.max_drawdown_pct / 100\nif drawdown >= drawdown_cap or drawdown + exposure > drawdown_cap:\n    return reject(f\"Drawdown or projected stop exposure exceeds {limits.max_drawdown_pct:g}% limit\", risk)\n"


def _tail_value(node, env):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id == "limits":
            return env["limits"][node.attr]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_tail_value(node.operand, env)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "max" and not node.keywords:
            return max(_tail_value(n, env) for n in node.args)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(_tail_value(n, env) for n in node.values)
    if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.GtE):
        return _tail_value(node.left, env) >= _tail_value(node.comparators[0], env)
    if isinstance(node, ast.BinOp):
        from act.research.funded_risk import OPS
        if type(node.op) in OPS:
            return OPS[type(node.op)](_tail_value(node.left, env),
                                      _tail_value(node.right, env))
    if isinstance(node, ast.Compare):
        from act.research.funded_risk import OPS
        if len(node.ops) == 1 and type(node.ops[0]) in OPS:
            return OPS[type(node.ops[0])](_tail_value(node.left, env),
                                         _tail_value(node.comparators[0], env))
    if isinstance(node, ast.JoinedStr):
        return "".join(_tail_value(n, env) for n in node.values)
    if isinstance(node, ast.FormattedValue) and node.conversion == -1:
        return format(_tail_value(node.value, env), _tail_value(node.format_spec, env))
    return _read(node, env)


def evaluate_remaining_risk(snapshot, proposal, account):
    """Complete reject mapping only; captured limits/formulas/reasons, no accepts.

    Strict tail AST pin rejects guard/reason/topology drift even on untaken paths.
    Earlier interpreters are retained unchanged; their reason strings are consumed.
    """
    try:
        policy = snapshot.policy
        source = policy["selection"]["named_production_rules"]["act.risk.limits"][
            "definitions"]["act.risk.limits.check_trade"]
        body = ast.parse(source).body[0].body
        statements = body[12:-1]
        expected = ast.parse(TAIL).body
        if ast.dump(ast.Module(body=statements, type_ignores=[])) != ast.dump(
                ast.Module(body=expected, type_ignores=[])):
            raise UnsupportedPolicy("Captured remaining-risk guard/reason drift")
        limits = policy["entry"]["risk_limits"]
        if not all(type(v) in (int, float) and math.isfinite(v) and 0 < v <= 100
                   for v in limits.values()):
            raise UnsupportedPolicy("Unsupported risk limits")
        for row in (proposal, account):
            if any(isinstance(v, (int, float)) and not math.isfinite(v)
                   for v in row.values()):
                raise UnsupportedPolicy("Nonfinite input")
        try:
            return evaluate_funded_risk(snapshot, proposal, account)
        except UnsupportedPolicy as exc:
            if str(exc) != "Later risk/sizing/acceptance NOT implemented; dispatch OFF":
                raise
        env = {"proposal": proposal, "account": account, "limits": limits,
               **{k: policy["costs"]["effective"][k]
                  for k in ("fee_pct", "slippage_pct")}}
        prefix = body[5:12]
        if tuple(ast.unparse(n.test if isinstance(n, ast.If) else n)
                 for n in prefix) != PREFIX:
            raise UnsupportedPolicy("Captured funded-risk drift")
        for node in prefix:
            if isinstance(node, ast.Assign):
                env[node.targets[0].id] = _read(node.value, env)
        for node in statements:
            if isinstance(node, ast.Assign):
                env[node.targets[0].id] = _tail_value(node.value, env)
            elif _tail_value(node.test, env):
                call = node.body[0].value
                return {"accepted": False, "reason": _tail_value(call.args[0], env),
                        "risk_usd": _tail_value(call.args[1], env)}
    except (KeyError, TypeError, IndexError, SyntaxError, ZeroDivisionError) as exc:
        raise UnsupportedPolicy("Unsupported remaining-risk input/capture") from exc
    raise UnsupportedPolicy("Acceptance/sizing/strategy NOT implemented; dispatch OFF")
