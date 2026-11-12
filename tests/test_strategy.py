"""Offline strategy acceptance, cost sizing and real paper executor integration."""
from datetime import datetime, timedelta, timezone

import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, SecurityAssessment, TokenDiscovery, TradeProposal
from act.paper.ledger import PaperLedger
from act.risk.limits import RiskSettings
from act.results import capture_results, results_panels
from act.strategy import StrategySettings, evaluate, run_strategy as original_run_strategy

AT = datetime.now(timezone.utc)
PROFILE = {"status": "available", "poc": 1.8, "val": 1.7, "vah": 1.9,
           "value_area_pct": 70, "actual_value_area_pct": 75,
           "candle_count": 12, "timeframe": "5m"}


def run_strategy(pools, *args, **kwargs):
    kwargs.setdefault("volume_profiles", {p.pool_address: PROFILE for p in pools})
    return original_run_strategy(pools, *args, **kwargs)



def pool(**updates):
    values = dict(symbol="TEST", mint_address="mint", pool_address="pool",
                  first_seen_at=AT - timedelta(hours=1), price_usd=2,
                  liquidity_usd=10000, volume_24h_usd=25000,
                  buys_24h=110, sells_24h=100, source_url="https://example.org/pool")
    values.update(updates)
    return TokenDiscovery(**values)


def security(**updates):
    values = dict(score=1, lp_locked_pct=80, mint_authority_revoked=True,
                  freeze_authority_revoked=True, top_holder_concentration_pct=12,
                  source="offline fixture")
    values.update(updates)
    return SecurityAssessment(**values)


def account(**updates):
    values = dict(equity=10000, cash=10000, day_start_equity=10000, peak_equity=10000)
    values.update(updates)
    return AccountState(**values)


def decision(p=None, s=None, **kwargs):
    return evaluate(p or pool(), s or security(), cycle_id="cycle", at=AT,
                    account=kwargs.pop("account", account()),
                    volume_profile=kwargs.pop("volume_profile", PROFILE), **kwargs)


def test_accept_and_sizing():
    d = decision()
    assert d.decision == "BUY"
    p = d.proposal
    # Deliberate new regime: fraction floor is anchored to slipped entry.
    stop = 2 * 1.005 * 0.85
    assert p["nominal_position_notional_usd"] == pytest.approx(100 / ((2 - stop) / 2))
    assert p["stop_loss"] == stop
    assert p["take_profit"] == pytest.approx(2.8)
    # The notional cap now binds instead of exhausting the 1%-at-stop budget.
    size = 200 / (2 * 1.005) * (1 - 1e-10)
    assert p["size"] == pytest.approx(size)
    assert p["slipped_entry_notional_usd"] <= 200
    assert p["slipped_entry_notional_usd"] == pytest.approx(200)
    assert p["max_estimated_loss"] == pytest.approx(
        size * (2 * 1.005 * 1.0025 - stop * 0.995 * 0.9975))
    assert p["estimated_fees"] > 0 and p["estimated_slippage"] > 0
    assert p["risk_reward_ratio"] > 2
    assert p["data_timestamp"] == AT.isoformat()
    assert "score=1" in d.reasoning and "mint" in d.reasoning


@pytest.mark.parametrize("field,value", [
    ("score", 500), ("score", 126047), ("lp_locked_pct", 79.99),
    ("mint_authority_revoked", False), ("freeze_authority_revoked", False),
])
def test_security_reject(field, value):
    assert decision(s=security(**{field: value})).decision == "REJECT"


@pytest.mark.parametrize("field", ["score", "lp_locked_pct", "mint_authority_revoked",
                                   "freeze_authority_revoked", "top_holder_concentration_pct"])
def test_missing_security_never_passes(field):
    d = decision(s=security(**{field: None}))
    assert d.decision == "REJECT" and field in d.reasoning
    assert d.proposal is None


@pytest.mark.parametrize("updates", [
    {"price_usd": None}, {"liquidity_usd": 9999}, {"volume_24h_usd": 24999},
    {"liquidity_usd": None}, {"volume_24h_usd": None}, {"buys_24h": None},
    {"sells_24h": None}, {"sells_24h": 0}, {"buys_24h": 109},
    {"first_seen_at": AT - timedelta(hours=73)},
    {"first_seen_at": AT + timedelta(seconds=1)},
    {"unverified_fields": ["symbol"]},
])
def test_market_reject(updates):
    assert decision(p=pool(**updates)).decision == "REJECT"


def test_boundary_age_and_score():
    assert decision(p=pool(first_seen_at=AT - timedelta(hours=72)),
                    s=security(score=499)).decision == "BUY"


@pytest.mark.parametrize("state,reason", [
    (account(equity=9700), "Daily loss"),
    (account(equity=8900, day_start_equity=8900), "Drawdown"),
    (account(cash=1), "Insufficient cash"),
])
def test_existing_risk_gate(state, reason):
    d = decision(account=state)
    assert d.decision == "REJECT" and reason in d.reasoning


def test_executor_integration_and_history(tmp_path):
    ledger = PaperLedger(tmp_path / "ledger.json")
    settings = Settings(_env_file=None)
    ds, prices = run_strategy([pool()], {"mint": security()}, ledger, settings, "cycle", at=AT)
    assert ds[0]["decision"] == "BUY" and len(ledger.positions) == 1
    assert ledger.account_state(prices).equity < 10000
    prefix = ledger.history
    position = next(iter(ledger.positions.values()))
    ledger.close(position.id, 1.5, "offline loss fixture")
    assert ledger.history[:len(prefix)] == prefix
    assert ledger.closed_trades[0].realized_pnl < 0
    assert len(PaperLedger(ledger.path).closed_trades) == 1


def test_rejected_opens_nothing(tmp_path):
    ledger = PaperLedger(tmp_path / "ledger.json")
    ds, _ = run_strategy([pool()], {}, ledger, Settings(_env_file=None), "cycle", at=AT)
    assert ds[0]["decision"] == "REJECT" and not ledger.positions


def test_concurrent_cap(tmp_path):
    ledger = PaperLedger(tmp_path / "ledger.json")
    for i in range(3):
        ledger.open(TradeProposal(token=f"held{i}", entry_price=2, size=1,
                                  stop_loss=1.7, take_profit=2.8, reason="offline cap fixture"))
    pools = [pool(mint_address=f"held{i}", pool_address=f"p{i}") for i in range(3)] + [pool()]
    ds, _ = run_strategy(pools, {p.mint_address: security() for p in pools}, ledger,
                         Settings(_env_file=None), "cycle", at=AT)
    assert len(ds) == 4 and len(ledger.positions) == 3
    assert ds[-1]["decision"] == "REJECT" and "cap 3" in ds[-1]["reasoning"]


def test_missing_marks_watch_every_candidate(tmp_path):
    ledger = PaperLedger(tmp_path / "ledger.json")
    ledger.open(TradeProposal(token="old", entry_price=2, size=1, stop_loss=1.7, reason="fixture"))
    ds, _ = run_strategy([pool()], {}, ledger, Settings(_env_file=None), "cycle", at=AT)
    assert ds[0]["decision"] == "WATCH" and "old" in ds[0]["reasoning"]
    assert len(ledger.positions) == 1


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("STRATEGY_MAX_RUGCHECK_SCORE", "100")
    monkeypatch.setenv("STRATEGY_STOP_FRACTION", "0.2")
    cfg = StrategySettings()
    assert cfg.max_rugcheck_score == 100 and cfg.stop_fraction == 0.2
    assert decision(s=security(score=100), config=cfg).decision == "REJECT"


@pytest.mark.parametrize("equity,slippage", [(10000, 0), (9411.3, .005), (500, .02)])
def test_notional_cap_binds_before_proposal(equity, slippage, monkeypatch):
    monkeypatch.setenv("RISK_MAX_POSITION_NOTIONAL_FRACTION", "0.02")
    d = decision(account=account(equity=equity, day_start_equity=equity,
                                 peak_equity=equity), slippage=slippage)
    assert d.decision == "BUY"
    p = d.proposal
    executed = p["size"] * p["entry_price"] * (1 + slippage)
    assert executed <= equity * .02
    assert executed == pytest.approx(equity * .02)
    assert p["position_notional_cap_usd"] == equity * .02
    assert p["max_estimated_loss"] < equity * .01
    assert p["worst_case_loss_bound_usd"] == pytest.approx(executed * 1.0025)


def test_notional_cap_does_not_bind_preserves_cost_risk(monkeypatch):
    monkeypatch.setenv("RISK_MAX_POSITION_NOTIONAL_FRACTION", "0.5")
    d = decision()
    assert d.decision == "BUY"
    p = d.proposal
    entry_cost = 2 * 1.005 * 1.0025
    stop_proceeds = p["stop_loss"] * .995 * .9975
    expected = min(p["nominal_position_notional_usd"] / 2,
                   100 / (entry_cost - stop_proceeds)) * (1 - 1e-10)
    assert p["size"] == pytest.approx(expected)
    assert p["max_estimated_loss"] == pytest.approx(100)
    assert p["slipped_entry_notional_usd"] < 5000


@pytest.mark.parametrize("val", [1.5, 1.8])
def test_stop_anchor_survives_executor_ledger_and_replay(tmp_path, monkeypatch, val):
    monkeypatch.setenv("RISK_MAX_POSITION_NOTIONAL_FRACTION", "0.02")
    ledger = PaperLedger(tmp_path / "anchor.json")
    ds, _ = run_strategy([pool()], {"mint": security()}, ledger, Settings(_env_file=None),
                         "anchor", at=AT, volume_profiles={"pool": dict(PROFILE, val=val)})
    assert ds[0]["decision"] == "BUY"
    p = next(iter(ledger.positions.values()))
    assert p.stop_loss == max(val, p.entry_price * .85)
    assert p.stop_loss == ds[0]["proposal"]["stop_loss"]
    assert p.entry_price * p.size <= 200
    replayed = PaperLedger(ledger.path).positions[p.id]
    assert replayed.stop_loss == p.stop_loss
    prefix = ledger.history
    bound = ds[0]["proposal"]["worst_case_loss_bound_usd"]
    # Near-zero executable quote: a stop is not a floor; funded principal bounds loss.
    closed = ledger.close(p.id, 1e-30, "offline gap-to-near-zero")
    assert 0 < -closed.realized_pnl <= bound
    assert ledger.history[:len(prefix)] == prefix
    assert PaperLedger(ledger.path).closed_trades[0].realized_pnl == closed.realized_pnl


def test_first_trade_quote_anchor_regression(tmp_path):
    d = decision(p=pool(price_usd=.0003818), volume_profile=dict(
        PROFILE, val=.0003, vah=.00035, poc=.00032))
    assert d.decision == "BUY"
    proposal = TradeProposal(**{k: v for k, v in d.proposal.items()
                              if k in TradeProposal.model_fields})
    ledger = PaperLedger(tmp_path / "first-quote.json")
    p = ledger.open(proposal)
    assert p.entry_price == pytest.approx(.000383709)
    assert p.stop_loss == p.entry_price * .85
    assert p.stop_loss == pytest.approx(.00032615265)


@pytest.mark.parametrize("value", ["0", "-0.02", "1.01", "nan", "inf", "-inf", "bad"])
def test_invalid_notional_fraction_fails_loudly(monkeypatch, value):
    monkeypatch.setenv("RISK_MAX_POSITION_NOTIONAL_FRACTION", value)
    with pytest.raises(ValueError):
        RiskSettings()
    with pytest.raises(ValueError):
        decision()


def test_notional_config_snapshot_and_historical_display(monkeypatch):
    monkeypatch.setenv("RISK_MAX_POSITION_NOTIONAL_FRACTION", "0.03")
    report = {"paper_account": {"equity": 10000, "cash": 10000}, "paper_positions": []}
    capture_results(report)
    assert report["results_config"]["RISK_MAX_POSITION_NOTIONAL_FRACTION"] == .03
    html = results_panels(report, [])
    assert "USD 300.000000 + modeled entry fee" in html
    # Never backfill today's cap into an old cycle with no recorded cap.
    old = {"paper_account": {"equity": 10000}, "results_config": {}}
    assert "n/a (not recorded)" in results_panels(old, [])
    assert "USD 300.000000" not in results_panels(old, [])
    report["results_config"]["RISK_MAX_POSITION_NOTIONAL_FRACTION"] = "<script>bad()</script>"
    html = results_panels(report, [])
    assert "<script>bad()" not in html and "&lt;script&gt;" in html
    assert "n/a (not recorded)" in html
