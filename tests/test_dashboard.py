"""Offline dashboard regression tests; no lifespan or HTTP clients started."""
from act import api


def test_dashboard_position_fields_and_closed_trade(monkeypatch):
    monkeypatch.setattr(api, "LATEST", {
        "paper_account": {"equity": 10001},
        "valuation_status": "fresh_cycle_quotes",
        "paper_positions": [{"token": "<unsafe>", "current_price": 12,
                             "unrealized_pnl": 1.25, "stop_loss": 9.125,
                             "take_profit": 15.875, "stop_distance_pct": 23,
                             "target_distance_pct": 32, "age_hours": 4,
                             "stale_quoted": False}],
        "paper_trades": [{"token": "TEST", "exit_reason": "stop_loss",
                          "exit_price_basis": 9.125, "exit_price": 9.08,
                          "realized_pnl": -2.75,
                          "opened_at": "2026-11-01", "closed_at": "2026-11-02"}],
    })
    page = api.dashboard()
    for text in ("9.125", "15.875", "1.25", "-2.75", "stop_loss",
                 "fresh_cycle_quotes", "&lt;unsafe&gt;", "SIMULATED - PAPER TRADING ONLY"):
        assert text in page
    assert "<unsafe>" not in page


def test_dashboard_stale_has_no_fabricated_equity(monkeypatch):
    monkeypatch.setattr(api, "LATEST", {
        "paper_account": None,
        "valuation_status": "unavailable_missing_position_quotes",
        "paper_positions": [{"token": "STALE", "current_price": None,
                             "unrealized_pnl": None, "stale_quoted": True}],
    })
    page = api.dashboard()
    assert "Equity USD: Unavailable" in page
    assert "unavailable_missing_position_quotes" in page
    assert "True" in page
    assert "No closed paper trades; no live exit demonstrated." in page


def test_dashboard_empty_state(monkeypatch):
    monkeypatch.setattr(api, "LATEST", None)
    page = api.dashboard()
    assert "No open paper positions." in page
    assert "No closed paper trades; no live exit demonstrated." in page
