"""Offline synthetic fixtures only. No lifespan, collection, persistence or live HTTP."""
import copy
import re

import httpx
import pytest

from act import api
from act.hud import budget_bar, chart, hero, money, recorded_events, restyle, timestamp


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('HUD rendering must not use the network')
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)


def snapshot(at, equity):
    return {'equity_snapshot': {'timestamp': at, 'equity': equity}}


def test_loss_is_red_and_rounded_without_changing_records():
    report = {'paper_account': {'equity': 9411.304471871887}, 'paper_trades': [
        {'realized_pnl': -588.6955281281129}], 'paper_positions': []}
    before = copy.deepcopy(report)
    html = hero(report, [])
    assert '<strong class="negative">-$588.70</strong>' in html
    assert '<small class="negative">-5.9%' in html
    assert '$9,411.30' in html
    assert 'SMALL-N: win rate n=1' in html
    assert '0.0%' in html
    assert report == before


def test_unknown_aggregates_and_equity_are_not_zero_filled():
    html = hero({}, [])
    assert '<strong>Unavailable</strong>' in html
    assert '<strong class="">Unavailable</strong>' in html
    assert 'insufficient data' in html
    assert '<b>n/a</b>' in html
    assert 'Budget ratio: n/a' in html
    assert 'win rate n=n/a' in html
    # Only the explicit fixed free-tier cost is $0.00, not unknown performance.
    assert html.count('$0.00') == 1


@pytest.mark.parametrize('value', [None, True, float('nan'), float('inf'), '100'])
def test_non_numeric_money_is_unavailable(value):
    assert money(value) == 'Unavailable'


def test_empty_flat_and_single_point_chart():
    assert 'No equity snapshots' in chart([])
    row = snapshot('2026-11-10T16:00:00Z', 10000)
    html = chart([row], True)
    assert html.count('<circle ') == 1
    assert 'nan' not in html.lower() and 'infinity' not in html.lower()
    assert 'no synthetic backfill' in html
    html = chart([row, snapshot('2026-11-10T16:20:00Z', 10000)], True)
    assert html.count('<polyline ') == 1
    assert '0 gap markers' in html


def test_elapsed_time_gap_breaks_line_in_both_scales():
    rows = [snapshot('2026-11-10T16:00:00Z', 10000),
            snapshot('2026-11-10T16:20:00Z', 9990),
            snapshot('2026-11-11T08:00:00Z', 9411)]
    before = copy.deepcopy(rows)
    for logarithmic in (False, True):
        html = chart(list(reversed(rows)), logarithmic)
        assert html.count('<polyline ') == 2
        assert html.count('class="gap-marker"') == 1
        assert 'no interpolation' in html
        segments = re.findall(r'<polyline points="([^"]+)"', html)
        assert len(segments[0].split()) == 2
        assert len(segments[1].split()) == 1
    assert rows == before


def test_missing_nonfinite_and_duplicate_valuations_break_line():
    for value in (None, float('nan'), float('inf'), True):
        html = chart([snapshot('2026-11-10T16:00:00Z', 10000),
                      snapshot('2026-11-10T16:10:00Z', value),
                      snapshot('2026-11-10T16:20:00Z', 9990)])
        assert html.count('<circle ') == 2
        assert html.count('<polyline ') == 2
        assert '1 unavailable (gaps)' in html
    html = chart([snapshot('2026-11-10T16:00:00Z', 10000),
                  snapshot('2026-11-10T16:00:00Z', 9990)])
    assert html.count('<polyline ') == 2


@pytest.mark.parametrize('value', [0, -10])
def test_log_scale_rejects_nonpositive_but_linear_keeps_it(value):
    rows = [snapshot('2026-11-10T16:00:00Z', value)]
    assert 'Log scale unavailable' in chart(rows, True)
    assert '<circle ' in chart(rows, False)


def test_invalid_timestamp_does_not_crash_or_claim_history():
    assert timestamp('<bad>') is None
    assert timestamp('2026-11-10T16:00:00') is None
    html = chart([snapshot('<bad>', 10000), snapshot('2026-11-10T16:00:00Z', 9990)])
    assert '1 invalid timestamps omitted' in html
    assert html.count('<circle ') == 1


def test_annotations_use_recorded_events_and_escape_symbols():
    trade = {'token': 'mint', 'opened_at': '2026-11-10T16:01:00Z',
             'closed_at': '2026-11-10T16:09:00Z', 'exit_reason': 'stop_loss',
             'realized_pnl': -20}
    report = {'paper_trades': [trade]}
    reports = [{'decisions': [{'mint_address': 'mint', 'symbol': '<unsafe>'}]}]
    events = recorded_events(report, reports)
    assert events == [(trade['opened_at'], 'First retained paper BUY · <unsafe>'),
                      (trade['closed_at'], 'stop_loss · -$20.00')]
    rows = [snapshot('2026-11-10T16:00:00Z', 10000),
            snapshot('2026-11-10T16:10:00Z', 9980)]
    html = chart(rows, True, events)
    assert '&lt;unsafe&gt;' in html and '<unsafe>' not in html
    assert 'stop_loss · -$20.00' in html
    assert recorded_events({}) == []
    assert 'PIVOT' not in html


def test_real_budget_ratios_only_and_escaping():
    assert '<meter ' in budget_bar(0, 24)
    assert 'max="24" value="6"' in budget_bar(6, 24)
    for used, cap in [(None, 24), (0, 0), (-1, 24), (True, 24), (1, None)]:
        assert '<meter ' not in budget_bar(used, cap)
    html = hero({'discovery_stats': {'geckoterminal_trending': {
        'status': 'failed', 'kept_after_cap': 0, 'errors': ['HTTP 429 <unsafe>']}},
        'security_source_stats': {'rpc_calls': 0, 'budget': 24}}, [])
    assert 'HTTP 429 &lt;unsafe&gt;' in html
    assert '<unsafe>' not in html
    assert 'Budget used / cap: 0 / 24' in html
    for name in ('01 // DATA SOURCE', '02 // DATA SOURCE', '03 // DATA SOURCE',
                 'GeckoTerminal', 'DexScreener', 'RugCheck fallback'):
        assert name in html


def test_existing_panel_provenance_preserved_and_old_chart_replaced():
    provenance = '<details><summary>Exact records</summary><pre>&lt;unsafe&gt;</pre></details>'
    page = '<style></style><main><section id="equity"><h2>Equity</h2>OLD_CHART' + provenance + '</section></main>'
    html = restyle(page, {}, [])
    assert provenance in html
    assert 'OLD_CHART' not in html
    assert '<details class="view" id="equity"><summary>// Paper equity curve' in html


def test_nine_research_views_and_extra_audits_remain_no_external_assets(monkeypatch):
    monkeypatch.setattr(api, 'LATEST', None)
    monkeypatch.setattr(api, 'RECENT', [])
    monkeypatch.setattr(api, 'HISTORY_ERROR', '<failed>')
    html = api.dashboard()
    for identifier in ('equity', 'positions', 'trades', 'decision-log', 'risk',
                       'cycle-trends', 'intelligence', 'learning', 'readiness',
                       'audit', 'decisions'):
        assert f'<details class="view" id="{identifier}">' in html
    assert '&lt;failed&gt;' in html
    assert 'PAPER MODE' in html
    assert '<script' not in html and 'https://' not in html
    assert 'cdn' not in html.lower()
    assert api.status()['paper_only'] is True
    for route in api.app.routes:
        assert not (set(route.methods or []) & {'POST', 'PUT', 'PATCH', 'DELETE'})


def test_window_counts_not_lifetime_and_completed_not_missed():
    report = {'decisions': [], 'learning': {'stats': {
        'tracked_ever': 12, 'pending': 30, 'terminal_in_view': 6,
        'status_counts': {'observed': 1, 'partial': 2, 'missed_service_gap_or_budget': 3}}}}
    html = hero(report, [report])
    assert 'observed / partial in view</small><b>3</b>' in html
    assert 'terminal in view</small><b>6</b>' in html
    assert 'not lifetime' in html
