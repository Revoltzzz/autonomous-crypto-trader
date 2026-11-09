"""Offline strategy acceptance, cost sizing and real paper executor integration."""
from datetime import datetime, timedelta, timezone

import pytest

from act.config.settings import Settings
from act.domain.models import AccountState, SecurityAssessment, TokenDiscovery, TradeProposal
from act.paper.ledger import PaperLedger
from act.strategy import StrategySettings, evaluate, run_strategy

AT = datetime.now(timezone.utc)


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
                    account=kwargs.pop("account", account()), **kwargs)


def test_accept_and_sizing():
    d = decision()
    assert d.decision == "BUY"
    p = d.proposal
    assert p["nominal_position_notional_usd"] == pytest.approx(100 / 0.15)
    assert p["stop_loss"] == pytest.approx(1.7)
    assert p["take_profit"] == pytest.approx(2.8)
    assert p["max_estimated_loss"] == pytest.approx(100)
    assert p["size"] * 2 < 100 / 0.15
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
