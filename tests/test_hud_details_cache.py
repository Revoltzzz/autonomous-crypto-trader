"""Offline hero details and persisted cold-start cache; no collection or HTTP."""
import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from act import api
from act.hud import CSS, chart, detail_cards, hero


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No network in dashboard tests')
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)
    monkeypatch.setattr(api, 'LATEST', None)
    monkeypatch.setattr(api, 'RECENT', [])
    monkeypatch.setattr(api, 'HISTORY_ERROR', None)


def stored():
    return json.loads(Path(__file__).with_name('hud_stored_cycle.json').read_text())


def test_recorded_empty_is_not_unknown():
    known = detail_cards({'paper_positions': [], 'paper_trades': []}, [])
    unknown = detail_cards({}, [])
    assert '0 OPEN POSITIONS' in known
    assert 'Showing 0 of 0' in known
    assert 'Positions unavailable' in unknown
    assert 'Closed trades unavailable' in unknown
    assert '0 OPEN POSITIONS' not in unknown
    assert 'n/a (not recorded)' in unknown


def test_real_single_trade_loss_duration_and_immutability():
    report = stored()
    before = copy.deepcopy(report)
    html = detail_cards(report, [report])
    assert 'Showing 1 of 1 recorded closed trades' in html
    assert 'SMALL-N: n=1' in html
    assert 'stop_loss' in html
    assert 'class="negative">-$588.70' in html
    assert '25.86 hours' in html
    assert 'net P&amp;L / entry notional' in html
    assert report == before


def test_last_five_closed_trades_are_timestamp_ordered():
    rows = [{'token': f'token{i}', 'closed_at': f'2026-11-12T0{i}:00:00Z'} for i in range(6)]
    html = detail_cards({'paper_trades': rows}, [])
    assert 'Showing 5 of 6' in html
    assert 'token0' not in html
    assert html.index('token5') < html.index('token4') < html.index('token1')
    assert 'Hold: n/a' in html


@pytest.mark.parametrize('stale,mark', [(True, 5), (False, None)])
def test_unavailable_or_stale_valuation_never_zero_filled(stale, mark):
    position = {'token': '<unsafe>' + 'x' * 500, 'entry_price': 2, 'size': 10,
                'current_price': mark, 'unrealized_pnl': 20, 'stale_quoted': stale,
                'stop_loss': 1, 'take_profit': 4, 'stop_distance_pct': 50,
                'target_distance_pct': 100, 'quote': {'observed_at': '<time>'}}
    html = detail_cards({'paper_positions': [position]}, [])
    assert '&lt;unsafe&gt;' in html and '<unsafe>' not in html
    assert '&lt;time&gt;' in html
    assert 'Current mark: unavailable / stale' in html
    assert 'Unrealized: Unavailable' in html
    assert 'recorded distance: n/a' in html
    assert '$0.00' not in html
    assert 'overflow-wrap:anywhere' in CSS


def test_available_position_values_and_recorded_risk_only():
    position = {'token': 'mint', 'entry_price': 2, 'size': 10, 'current_price': 3,
                'unrealized_pnl': 9, 'stale_quoted': False, 'stop_loss': 1,
                'take_profit': 4, 'stop_distance_pct': 66.6667, 'target_distance_pct': 33.3333}
    report = {'paper_positions': [position], 'paper_account': {'equity': 9411.3},
              'results_config': {'RISK_MAX_POSITION_NOTIONAL_FRACTION': .02,
                                 'STRATEGY_MAX_OPEN_POSITIONS': 3}}
    html = detail_cards(report, [])
    assert 'Current mark: 3' in html
    assert '$9.00' in html and '+45.00%' in html
    assert '+66.67%' in html and '+33.33%' in html
    assert 'USD 188.226000 + modeled entry fee' in html
    assert 'Open / maximum: 1 / 3' in html
    assert 'Not a guaranteed stop fill' in html
    report['results_config'] = {}
    assert 'n/a (not recorded)' in detail_cards(report, [])
    assert '188.226' not in detail_cards(report, [])


def snap(at, equity):
    return {'equity_snapshot': {'timestamp': at, 'equity': equity}}


def test_chart_last_extremes_grid_labels_and_gap_segments():
    rows = [snap('2026-11-10T16:00:00Z', 10000),
            snap('2026-11-10T16:20:00Z', 9900),
            snap('2026-11-11T16:20:00Z', 9411.3)]
    for log in (False, True):
        html = chart(rows, log)
        assert 'class="last-value"' in html
        assert 'LAST $9,411.30' in html
        assert 'MIN $9,411.30' in html and 'MAX $10,000.00' in html
        assert '2026-11-11T16:20:00Z' in html
        assert html.count('<polyline ') == 2
        assert html.count('class="gap-marker"') == 1
        assert 'font-size="12">$' in html
    rows.append(snap('2026-11-11T16:30:00Z', None))
    html = chart(rows)
    assert 'LAST unavailable' in html
    assert 'class="last-value"' not in html
    assert 'MIN $9,411.30' in html


def test_as_of_marker_is_escaped_and_absent_is_honest():
    html = hero({'finished_at': '<timestamp>'}, [])
    assert 'as of &lt;timestamp&gt;' in html
    assert 'not a live quote' in html
    assert 'No stored state available' in hero({}, [])


def test_cold_start_restores_persisted_record_before_first_cycle(monkeypatch):
    report = stored()
    calls = []
    store = object()
    monkeypatch.setattr(api, 'Settings', lambda: object())
    monkeypatch.setattr(api, 'get_persistence', lambda settings: store)
    def load(actual):
        assert actual is store
        calls.append('read')
        return [report]
    monkeypatch.setattr(api, 'load_reports', load)
    async def no_cycle():
        await asyncio.Event().wait()
    monkeypatch.setattr(api, 'research_loop', no_cycle)
    monkeypatch.setattr(api, 'setup_logging', lambda: None)
    async def scenario():
        async with api.lifespan(api.app):
            assert api.LATEST == report
            html = api.dashboard()
            assert '$9,411.30' in html and '-$588.70' in html
            assert 'as of ' + report['finished_at'] in html
            assert '0 OPEN POSITIONS' in html
            assert 'Showing 1 of 1' in html
            assert 'No equity snapshots available' not in html
            assert 'Learning sessions' in html
            assert api.status() == report
            for identifier in ('equity', 'positions', 'trades', 'risk', 'decision-log',
                               'cycle-trends', 'learning', 'intelligence', 'readiness'):
                assert f'id="{identifier}"' in html
            api.dashboard()
            assert calls == ['read']
    asyncio.run(scenario())


@pytest.mark.parametrize('fails', [False, True])
def test_empty_or_failed_startup_never_invents_state(monkeypatch, fails):
    monkeypatch.setattr(api, 'Settings', lambda: object())
    monkeypatch.setattr(api, 'get_persistence', lambda settings: object())
    def load(store):
        if fails:
            raise RuntimeError('offline failure')
        return []
    monkeypatch.setattr(api, 'load_reports', load)
    asyncio.run(api.warm_results_cache())
    assert api.LATEST is None and api.RECENT == []
    html = api.dashboard()
    assert '<strong>Unavailable</strong>' in html
    assert 'No stored state available' in html
    assert 'No equity snapshots available' in html
    assert bool(api.HISTORY_ERROR) == fails
