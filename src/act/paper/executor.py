"""Risk-gated simulated execution; never contacts a trading service."""
from act.config.settings import Settings
from act.domain.models import RiskCheckResult, TradeProposal
from act.paper.ledger import PaperLedger
from act.risk.limits import RiskLimits, check_trade


class PaperExecutor:
    def __init__(self, ledger: PaperLedger, settings: Settings | None = None,
                 limits: RiskLimits = RiskLimits()):
        self.ledger = ledger
        self.settings = settings or Settings()
        self.limits = limits

    def _guard(self):
        if self.settings.paper_only is not True:
            raise ValueError("Only paper execution is permitted")

    def open(self, proposal: TradeProposal, prices: dict[str, float]):
        self._guard()
        result = check_trade(
            proposal, self.ledger.account_state(prices), self.limits,
            fee_pct=self.ledger.fee * 100, slippage_pct=self.ledger.slippage * 100,
        )
        if not result.accepted:
            raise ValueError(result.reason)
        return self.ledger.open(proposal)

    def close(self, position_id: str, market_price: float, reason: str,
              risk_result: RiskCheckResult):
        self._guard()
        if not risk_result.accepted:
            raise ValueError(risk_result.reason)
        return self.ledger.close(position_id, market_price, reason)
