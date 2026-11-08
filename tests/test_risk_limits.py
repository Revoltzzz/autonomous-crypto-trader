import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, TradeProposal
from act.risk.limits import check_trade


def proposal(**changes):
    return TradeProposal(**({"token": "SOL", "entry_price": 100, "size": 1,
                             "stop_loss": 95, "reason": "offline test"} | changes))


def account(**changes):
    return AccountState(**({"equity": 10000, "cash": 10000,
                            "day_start_equity": 10000, "peak_equity": 10000} | changes))


def test_accept_funded_small_trade():
    result = check_trade(proposal(), account())
    assert result.accepted
    assert result.risk_usd > 5  # round-trip costs included


@pytest.mark.parametrize("trade,state,reason", [
    (proposal(size=20), account(), "1%"),
    (proposal(), account(equity=9699), "3%"),
    (proposal(), account(equity=8999, day_start_equity=8999), "10%"),
    (proposal(stop_loss=None), account(), "stop-loss"),
    (proposal(leverage=2), account(), "leverage"),
    (proposal(stop_loss=101), account(), "below"),
    (proposal(), account(cash=1), "cash"),
])
def test_reject_limits_with_written_reasons(trade, state, reason):
    result = check_trade(trade, state)
    assert not result.accepted
    assert reason in result.reason


def test_projected_daily_loss_rejected():
    result = check_trade(proposal(), account(daily_realized_pnl=-299))
    assert not result.accepted
    assert "3%" in result.reason


def test_false_paper_setting_is_rejected():
    with pytest.raises(ValueError, match="paper"):
        Settings(paper_only=False, _env_file=None)
