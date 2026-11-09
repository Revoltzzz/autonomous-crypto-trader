"""Single-writer local paper event ledger. No event/history deletion API."""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from act.domain.models import AccountState, PaperPosition, PaperTrade, TradeProposal


def utcnow():
    return datetime.now(timezone.utc)


def positive(value):
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Expected a positive finite price or balance")
    return value


class PaperLedger:
    def __init__(self, path, initial_cash=10000, fee_pct=0.25, slippage_pct=0.5):
        self.path = Path(path)
        self.fee = fee_pct / 100
        self.slippage = slippage_pct / 100
        if not all(math.isfinite(x) and 0 <= x < 1 for x in (self.fee, self.slippage)):
            raise ValueError("Invalid fee or slippage")
        self._disk = self.path.read_text() if self.path.exists() else None
        if self._disk is None:
            self._events = [{"kind": "init", "at": utcnow().isoformat(),
                             "cash": positive(initial_cash), "fee": self.fee,
                             "slippage": self.slippage}]
        else:
            self._events = json.loads(self._disk)
        if not self._events or self._events[0]["kind"] != "init":
            raise ValueError("Invalid ledger history")
        init = self._events[0]
        if init["fee"] != self.fee or init["slippage"] != self.slippage:
            raise ValueError("Existing ledger cost assumptions differ; do not overwrite history")
        self.initial_cash = positive(init["cash"])
        for event in self._events[1:]:
            if event["kind"] == "open":
                PaperPosition.model_validate(event["position"])
            elif event["kind"] == "close":
                PaperTrade.model_validate(event["trade"])
            elif event["kind"] not in ("mark", "position_quotes"):
                raise ValueError("Unknown ledger event")
        self.positions

    @property
    def history(self):
        return json.loads(json.dumps(self._events))

    @property
    def positions(self):
        positions = {}
        for event in self._events:
            if event["kind"] == "open":
                position = PaperPosition.model_validate(event["position"])
                if position.id in positions:
                    raise ValueError("Duplicate position event")
                positions[position.id] = position
            elif event["kind"] == "close":
                del positions[event["trade"]["id"]]
        return positions

    @property
    def closed_trades(self):
        return [PaperTrade.model_validate(e["trade"]) for e in self._events if e["kind"] == "close"]

    @property
    def realized_pnl(self):
        return sum(t.realized_pnl for t in self.closed_trades)

    @property
    def cash(self):
        return self.initial_cash + self.realized_pnl - sum(
            p.entry_price * p.size + p.fees_paid for p in self.positions.values())

    def _append(self, event):
        current = self.path.read_text() if self.path.exists() else None
        if current != self._disk:
            raise RuntimeError("Ledger changed externally; reload before writing")
        events = self._events + [event]
        text = json.dumps(events, indent=2, allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self._events, self._disk = events, text

    def record_position_quotes(self, observations, at):
        """Append this cycle's fresh/null marks, never change prior observations."""
        self._append({"kind": "position_quotes", "at": at.isoformat(),
                      "positions": observations})

    def open(self, proposal: TradeProposal) -> PaperPosition:
        if proposal.leverage != 1 or proposal.stop_loss is None:
            raise ValueError("Paper longs require a stop-loss and no leverage")
        if proposal.stop_loss >= proposal.entry_price:
            raise ValueError("Stop must be below entry")
        price = proposal.entry_price * (1 + self.slippage)
        fee = price * proposal.size * self.fee
        if price * proposal.size + fee > self.cash:
            raise ValueError("Insufficient paper cash")
        position = PaperPosition(
            token=proposal.token, pool_address=proposal.pool_address,
            entry_price=price, size=proposal.size,
            stop_loss=proposal.stop_loss, take_profit=proposal.take_profit,
            fees_paid=fee, opened_at=utcnow(), strategy=proposal.strategy, reason=proposal.reason)
        self._append({"kind": "open", "at": position.opened_at.isoformat(),
                      "position": position.model_dump(mode="json")})
        return position

    def close(self, position_id: str, market_price: float, reason: str) -> PaperTrade:
        if not reason.strip():
            raise ValueError("An exit reason is required")
        position = self.positions[position_id]
        price = positive(market_price) * (1 - self.slippage)
        fees = position.fees_paid + price * position.size * self.fee
        values = position.model_dump()
        values["fees_paid"] = fees
        trade = PaperTrade(**values, exit_price=price, exit_price_basis=market_price,
                           closed_at=utcnow(), exit_reason=reason,
                           realized_pnl=(price - position.entry_price) * position.size - fees)
        self._append({"kind": "close", "at": trade.closed_at.isoformat(),
                      "trade": trade.model_dump(mode="json")})
        return trade

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum((positive(prices[p.token]) * (1 - self.slippage) * (1 - self.fee)
                    - p.entry_price) * p.size - p.fees_paid for p in self.positions.values())

    def account_state(self, prices: dict[str, float]) -> AccountState:
        now = utcnow()
        equity = self.initial_cash + self.realized_pnl + self.unrealized_pnl(prices)
        marks = [e for e in self._events if e["kind"] == "mark"]
        today = [e for e in marks if e["at"][:10] == now.date().isoformat()]
        previous = marks[-1]["equity"] if marks else self.initial_cash
        day_start = today[0]["day_start"] if today else previous
        peak = max([self.initial_cash, equity] + [e["equity"] for e in marks])
        daily = sum(t.realized_pnl for t in self.closed_trades if t.closed_at.date() == now.date())
        risk = sum(max(0, positive(prices[p.token]) - p.stop_loss) * p.size
                   + p.stop_loss * p.size * (self.slippage + self.fee)
                   for p in self.positions.values())
        state = AccountState(equity=equity, cash=max(0, self.cash),
                             day_start_equity=day_start, peak_equity=peak,
                             daily_realized_pnl=daily, open_risk_usd=risk)
        self._append({"kind": "mark", "at": now.isoformat(),
                      "equity": equity, "day_start": day_start})
        return state
