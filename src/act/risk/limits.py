"""Conservative fully-funded long-only paper-trade limits."""
import math
from dataclasses import dataclass

from act.domain.models import AccountState, RiskCheckResult, TradeProposal


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 10.0

    def __post_init__(self):
        for value in vars(self).values():
            if not math.isfinite(value) or not 0 < value <= 100:
                raise ValueError("Risk limits must be finite percentages in (0, 100]")


def check_trade(proposal: TradeProposal, account: AccountState,
                limits: RiskLimits = RiskLimits(), *, fee_pct: float = 0.25,
                slippage_pct: float = 0.5) -> RiskCheckResult:
    def reject(reason, risk=0):
        return RiskCheckResult(accepted=False, reason=reason, risk_usd=risk)

    if not all(math.isfinite(x) and 0 <= x < 100 for x in (fee_pct, slippage_pct)):
        return reject("Invalid fee or slippage assumption")
    if proposal.leverage != 1:
        return reject("No leverage permitted")
    if proposal.stop_loss is None:
        return reject("A stop-loss invalidation point is required")
    if proposal.stop_loss >= proposal.entry_price:
        return reject("Long stop-loss must be below the quoted entry price")
    entry = proposal.entry_price * (1 + slippage_pct / 100)
    if proposal.take_profit is not None and proposal.take_profit <= entry:
        return reject("Take profit must exceed slipped entry")
    exit_at_stop = proposal.stop_loss * (1 - slippage_pct / 100)
    cost = entry * proposal.size * (1 + fee_pct / 100)
    proceeds = exit_at_stop * proposal.size * (1 - fee_pct / 100)
    risk = cost - proceeds
    if cost > account.cash:
        return reject("Insufficient cash; borrowing and leverage forbidden", risk)
    if risk > account.equity * limits.max_risk_per_trade_pct / 100:
        return reject(f"Trade risk exceeds {limits.max_risk_per_trade_pct:g}% limit", risk)
    daily_loss = max(0, account.day_start_equity - account.equity,
                     -account.daily_realized_pnl)
    daily_cap = account.day_start_equity * limits.max_daily_loss_pct / 100
    exposure = account.open_risk_usd + risk
    if daily_loss >= daily_cap or daily_loss + exposure > daily_cap:
        return reject(f"Daily loss or projected stop exposure exceeds {limits.max_daily_loss_pct:g}% limit", risk)
    drawdown = max(0, account.peak_equity - account.equity)
    drawdown_cap = account.peak_equity * limits.max_drawdown_pct / 100
    if drawdown >= drawdown_cap or drawdown + exposure > drawdown_cap:
        return reject(f"Drawdown or projected stop exposure exceeds {limits.max_drawdown_pct:g}% limit", risk)
    return RiskCheckResult(accepted=True, reason="Accepted: funded paper long with stop and risk within limits", risk_usd=risk)
