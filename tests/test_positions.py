"""Offline position lifecycle tests: all HTTP is an httpx MockTransport."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from act.config.settings import Settings
from act.domain.models import TradeProposal
from act.paper.ledger import PaperLedger
from act.paper.positions import (
    PositionQuoteCollector, PositionSettings, manage_positions, position_views, process_positions,
)


def holding(tmp_path, monkeypatch, age=0):
    at = datetime.now(timezone.utc)
    monkeypatch.setattr('act.paper.ledger.utcnow', lambda: at - timedelta(hours=age))
    ledger = PaperLedger(tmp_path / 'ledger.json')
    position = ledger.open(TradeProposal(token='mint', pool_address='pool', entry_price=100,
        size=1, stop_loss=90, take_profit=120, reason='offline'))
    monkeypatch.setattr('act.paper.ledger.utcnow', lambda: at)
    settings = Settings(_env_file=None, ledger_path=tmp_path / 'ledger.json')
    return ledger, position, settings, at


@pytest.mark.parametrize('quote,age,reason', [
    (89, 0, 'stop_loss'), (90, 0, 'stop_loss'), (120, 0, 'take_profit'),
    (130, 0, 'take_profit'), (110, 48, 'max_age'), (110, 49, 'max_age')])
def test_exits_costs_equity_and_append_only(tmp_path, monkeypatch, quote, age, reason):
    ledger, position, settings, at = holding(tmp_path, monkeypatch, age)
    prefix = ledger.history
    result = process_positions(ledger, settings, {'mint': quote}, at=at)
    trade = result['exits'][0]
    expected = (quote * .995 - 100 * 1.005) - (100 * 1.005 * .0025 + quote * .995 * .0025)
    assert trade['exit_reason'] == reason
    assert trade['exit_price_basis'] == quote
    assert trade['exit_price'] == pytest.approx(quote * .995)
    assert trade['realized_pnl'] == pytest.approx(expected)
    assert trade['closed_at'] == at.isoformat()
    assert not ledger.positions
    assert ledger.history[:len(prefix)] == prefix
    assert ledger.account_state({}).equity == pytest.approx(10000 + expected)
    restored = PaperLedger(ledger.path)
    assert restored.history == ledger.history and not restored.positions
    assert restored.closed_trades[0].id == position.id


def test_stale_skips_even_age_exit_and_recovers(tmp_path, monkeypatch):
    ledger, p, settings, at = holding(tmp_path, monkeypatch, 100)
    prefix = ledger.history
    result = process_positions(ledger, settings, {}, at=at)
    assert not result['exits'] and result['stale_tokens'] == ['mint']
    view = result['observations'][0]
    assert view['stale_quoted'] and view['current_price'] is None
    assert view['unrealized_pnl'] is None
    assert not any(e['kind'] == 'mark' for e in ledger.history[len(prefix):])
    assert p.id in ledger.positions
    result = process_positions(ledger, settings, {'mint': 110}, at=at)
    assert result['exits'][0]['exit_reason'] == 'max_age'
    assert not result['stale_tokens']


def test_net_marks_and_equity(tmp_path, monkeypatch):
    ledger, p, settings, at = holding(tmp_path, monkeypatch)
    result = process_positions(ledger, settings, {'mint': 110}, at=at)
    expected = (110 * .995 * .9975 - p.entry_price) - p.fees_paid
    assert not result['exits']
    assert position_views(ledger, {'mint': 110})[0]['unrealized_pnl'] == pytest.approx(expected)
    assert ledger.account_state({'mint': 110}).equity == pytest.approx(10000 + expected)
    assert result['observations'][0]['stop_distance_pct'] == pytest.approx(20 / 110 * 100)


def test_settings_env(monkeypatch):
    monkeypatch.setenv('POSITION_MAX_HOLDING_HOURS', '12')
    monkeypatch.setenv('POSITION_MAX_QUOTE_AGE_SECONDS', '60')
    assert PositionSettings().max_holding_hours == 12
    assert PositionSettings().max_quote_age_seconds == 60
    for value in (0, -1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            PositionSettings(max_holding_hours=value)


def test_gt_identity_and_cg_fallback_and_missing(tmp_path, monkeypatch):
    ledger, p, settings, at = holding(tmp_path, monkeypatch)
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if 'geckoterminal' in request.url.host:
            return httpx.Response(200, json={'data': {'attributes': {
                'address': 'pool', 'base_token_price_usd': '110'}, 'relationships': {
                'base_token': {'data': {'id': 'solana_mint'}},
                'quote_token': {'data': {'id': 'solana_other'}}}}})
        raise AssertionError('GT success must not call fallback')
    async def run(handler):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await manage_positions(ledger, settings, client=client)
    result = asyncio.run(run(handler))
    assert result['prices'] == {'mint': 110} and len(calls) == 1
    def fallback(request):
        if 'geckoterminal' in request.url.host:
            return httpx.Response(404)
        return httpx.Response(200, json={'mint': {'usd': 111, 'last_updated_at': at.timestamp()}})
    assert asyncio.run(run(fallback))['prices'] == {'mint': 111}
    def stale(request):
        if 'geckoterminal' in request.url.host:
            return httpx.Response(200, json={'data': {'attributes': {'address': 'WRONG'}}})
        return httpx.Response(200, json={'mint': {'usd': 999, 'last_updated_at': 1}})
    result = asyncio.run(run(stale))
    assert result['prices'] == {} and result['stale_tokens'] == ['mint']
    assert not result['exits']


def test_empty_portfolio_no_http(tmp_path):
    ledger = PaperLedger(tmp_path / 'empty.json')
    def fail(request):
        raise AssertionError('No request for empty portfolio')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            return await manage_positions(ledger, Settings(_env_file=None), client=client)
    assert asyncio.run(run())['open_before'] == 0


def test_quote_collector_rejects_invalid_price(tmp_path, monkeypatch):
    _, p, _, _ = holding(tmp_path, monkeypatch)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={}))) as client:
            with pytest.raises(ValueError, match='GeckoTerminal:.*CoinGecko:'):
                await PositionQuoteCollector(client=client).fetch(p, PositionSettings())
    asyncio.run(run())
