"""Offline presentation regressions and reproducible identical-input profiling.

Fixture: public /status cycle 6f6cafc5-fe2c-4897-bc63-0d79b9dde368,
finished 2026-11-12T14:12:43.012157+00:00. Captured November 12, 2026.
JSON formatting is normalized; this fixture is never loaded by the service.
The pre-HUD dashboard body is preserved in api.dashboard: replacing only its
final restyle call with identity measures that renderer, not a network GET.
One genuine stored cycle is used as both latest and the loaded history window;
this is not a claim to replay all retained live history or Render hardware.
"""
import copy
import json
import re
import statistics
from html import escape
from pathlib import Path
from time import perf_counter

from act import api
from act.hud import CSS, hero, restyle


def stored_cycle():
    return json.loads(Path(__file__).with_name('hud_stored_cycle.json').read_text())


def test_complete_long_error_and_identifier_inherit_wrapping(monkeypatch):
    error = 'HTTP429https://recorded.invalid/' + 'x' * 2000 + '<unsafe>'
    token = 'TOKEN' + 'a' * 2000
    report = {'collectors': {'GeckoTerminal': {'status': 'failed', 'error': error}},
              'discovery_stats': {'geckoterminal_trending': {
                  'status': 'failed', 'errors': [error]}},
              'paper_positions': [{'token': token}]}
    monkeypatch.setattr(api, 'LATEST', report)
    monkeypatch.setattr(api, 'RECENT', [])
    monkeypatch.setattr(api, 'HISTORY_ERROR', None)
    page = api.dashboard()
    assert 'main{overflow-wrap:anywhere}' in CSS
    assert CSS in page
    assert escape(error) in page and token in page
    assert '<unsafe>' not in page
    assert 'text-overflow:ellipsis' not in CSS
    assert 'line-clamp' not in CSS
    assert escape(error) in hero(report, [])


def test_frozen_public_cycle_and_evidence_preservation(monkeypatch):
    report = stored_cycle()
    assert report['cycle_id'] == '6f6cafc5-fe2c-4897-bc63-0d79b9dde368'
    assert report['paper_only'] is True
    assert report['persistence']['status'] == 'persisted'
    assert report['paper_account']['equity'] == 9411.3
    original = copy.deepcopy(report)
    monkeypatch.setattr(api, 'LATEST', report)
    monkeypatch.setattr(api, 'RECENT', [report])
    monkeypatch.setattr(api, 'HISTORY_ERROR', None)
    monkeypatch.setattr(api, 'restyle', lambda page, *args: page)
    before = api.dashboard()
    monkeypatch.setattr(api, 'restyle', restyle)
    after = api.dashboard()
    # Every original escaped provenance block remains available, not truncated.
    for block in re.findall(r'<pre>.*?</pre>', before, re.S):
        assert block in after
    assert report == original
    assert 'main{overflow-wrap:anywhere}' in after
    assert '-$588.70' in after and '-5.9%' in after
    for identifier in ('equity', 'positions', 'trades', 'decision-log', 'risk',
                       'cycle-trends', 'intelligence', 'learning', 'readiness'):
        assert f'<details class="view" id="{identifier}">' in after


def test_identical_stored_input_renderer_profile(monkeypatch, capsys):
    report = stored_cycle()
    monkeypatch.setattr(api, 'LATEST', report)
    monkeypatch.setattr(api, 'RECENT', [report])
    monkeypatch.setattr(api, 'HISTORY_ERROR', None)
    renderers = {'pre_hud': lambda page, *args: page, 'hud': restyle}
    samples = {name: [] for name in renderers}
    pages = {}
    # Warm each path, then alternate order to avoid systematic warm-cache bias.
    for renderer in renderers.values():
        monkeypatch.setattr(api, 'restyle', renderer)
        api.dashboard()
    for iteration in range(21):
        order = list(renderers) if iteration % 2 else list(reversed(renderers))
        for name in order:
            monkeypatch.setattr(api, 'restyle', renderers[name])
            start = perf_counter()
            pages[name] = api.dashboard()
            samples[name].append((perf_counter() - start) * 1000)
    summary = {'fixture_cycle': report['cycle_id'], 'latest_cycles': 1,
               'history_cycles': 1, 'iterations_per_renderer': 21,
               'scope': 'Python HTML generation only; no network or lifespan'}
    for name, page in pages.items():
        blocks = re.findall(r'<pre>.*?</pre>', page, re.S)
        provenance = sum(map(len, blocks))
        summary[name] = {
            'median_ms': round(statistics.median(samples[name]), 3),
            'min_ms': round(min(samples[name]), 3),
            'max_ms': round(max(samples[name]), 3),
            'html_characters': len(page), 'utf8_bytes': len(page.encode()),
            'pre_blocks': len(blocks), 'pre_html_characters': provenance,
            'other_html_characters': len(page) - provenance,
            'pre_percent': round(100 * provenance / len(page), 3)}
    # A reproducible diagnostic, not a flaky wall-clock threshold test.
    with capsys.disabled():
        print('\nHUD_IDENTICAL_INPUT_PROFILE ' + json.dumps(summary, sort_keys=True))
    assert samples['pre_hud'] and samples['hud']
    assert len(pages['hud']) > len(pages['pre_hud'])
