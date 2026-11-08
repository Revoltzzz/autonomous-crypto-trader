import pytest

from act.domain.models import RiskCheckResult, TradeProposal
from act.paper.executor import PaperExecutor
from act.paper.ledger import PaperLedger


def trade():
    return TradeProposal(token="SOL", entry_price=100, size=2, stop_loss=90,
                         take_profit=120, reason="test research", strategy="test")


def test_entry_exit_costs_and_persistence(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = PaperLedger(path)
    position = ledger.open(trade())
    entry = 100 * 1.005
    entry_fee = entry * 2 * 0.0025
    assert position.entry_price == pytest.approx(entry)
    assert position.fees_paid == pytest.approx(entry_fee)
    assert ledger.cash == pytest.approx(10000 - entry * 2 - entry_fee)
    exit_price = 110 * 0.995
    total_fees = entry_fee + exit_price * 2 * 0.0025
    expected = (exit_price - entry) * 2 - total_fees
    assert ledger.unrealized_pnl({"SOL": 110}) == pytest.approx(expected)
    closed = ledger.close(position.id, 110, "target research exit")
    assert closed.exit_price == pytest.approx(exit_price)
    assert closed.fees_paid == pytest.approx(total_fees)
    assert closed.realized_pnl == pytest.approx(expected)
    assert ledger.cash == pytest.approx(10000 + expected)
    assert ledger.unrealized_pnl({}) == 0
    reloaded = PaperLedger(path)
    assert reloaded.positions == {}
    assert len(reloaded.closed_trades) == 1
    assert reloaded.realized_pnl == pytest.approx(expected)
    assert len(reloaded.history) == 3
    assert reloaded.closed_trades[0].reason == "test research"
    assert reloaded.closed_trades[0].exit_reason == "target research exit"


def test_missing_marks_fail_and_history_is_defensive(tmp_path):
    ledger = PaperLedger(tmp_path / "ledger.json")
    ledger.open(trade())
    with pytest.raises(KeyError):
        ledger.unrealized_pnl({})
    snapshot = ledger.history
    snapshot.clear()
    assert len(ledger.history) == 2


def test_executor_rejects_unapproved_close(tmp_path):
    ledger = PaperLedger(tmp_path / "ledger.json")
    executor = PaperExecutor(ledger)
    position = executor.open(trade(), {})
    with pytest.raises(ValueError, match="rejected"):
        executor.close(position.id, 110, "exit", RiskCheckResult(accepted=False, reason="rejected"))
    assert position.id in ledger.positions


def test_external_changes_never_overwritten(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = PaperLedger(path)
    ledger.open(trade())
    path.write_text(path.read_text() + "\n")
    with pytest.raises(RuntimeError):
        ledger.close(next(iter(ledger.positions)), 110, "exit")
