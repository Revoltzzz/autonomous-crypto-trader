"""Offline volume-profile contract, mathematics and fail-closed regressions."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from act.volume_profile import (VolumeProfileSettings, VolumeProfileFetcher,
                                compute_volume_profile, profile_stop, price_position)
from test_strategy import pool, security, account, AT
from act.strategy import evaluate, collect_volume_profiles
from act.config.settings import Settings
from act.paper.ledger import PaperLedger

# Real public GeckoTerminal SOL/USDC response captured 2026-11-09.
# /networks/solana/pools/DJNtGuBGEQiUCWE8F981M2C3ZghZt2XLD8f2sQdZ6rsZ/ohlcv/minute
# ?aggregate=15&limit=12&currency=usd&token=base. No network in these tests.
CAPTURED = {
    "data": {"id": "bf7f6d59-bd43-49fb-b541-51e3caff2708",
             "type": "ohlcv_request_response", "attributes": {"ohlcv_list": [
        [1794231900,86.43633867923324,86.6820916019658,86.2173785,86.6780607415605,10238.957340034362],
        [1794231000,86.50466213750437,86.7654240599056,86.19274,86.43633867923324,102082.96056707585],
        [1794230100,86.7517058609089,86.7517058609089,86.29262,86.50466213750437,69365.03228650577],
        [1794229200,86.9637741344904,86.9637741344904,86.28996933858959,86.7517058609089,121507.726052099],
        [1794228300,86.4310136603353,86.9638729913487,86.4103747602762,86.9637741344904,149733.85326624737],
        [1794227400,86.46842534832648,86.90262363762218,86.4310136603353,86.4310136603353,112148.07396893913],
        [1794226500,86.18172141332893,86.6360102846732,85.9682688303942,86.46842534832648,75946.99627478645],
        [1794225600,86.21332500798175,86.42571488908848,85.96181818181819,86.18172141332893,114837.04583310225],
        [1794224700,86.36021576656002,86.58437676559168,86.03131313131308,86.21332500798175,61552.971175300976],
        [1794223800,86.59015569837136,86.6789511461672,86.0759493670886,86.36021576656002,111475.79301220356],
        [1794222900,85.88498594156611,87.09008524690879,85.6215687435874,86.59015569837136,368465.94446543633],
        [1794222000,86.0269814106221,86.16162763471249,85.71054258149469,85.88498594156611,84303.66853476714]
    ]}},
    "meta": {
        "base": {"name": "Wrapped SOL", "symbol": "SOL", "coingecko_coin_id": "wrapped-solana",
                 "address": "So11111111111111111111111111111111111111112"},
        "quote": {"name": "USD Coin", "symbol": "USDC", "coingecko_coin_id": "usd-coin",
                  "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}}}


def test_hand_computed_profile_and_expansion():
    # Four width-1 bins have volumes 10, 50, 30, 10. POC bin 2;
    # select right neighbor (30) -> 80% value area, [2,4].
    rows = [[i, p, p, p, p, v] for i, (p, v) in enumerate(
        [(1,10), (2,50), (3,30), (5,10)])]
    p = compute_volume_profile(rows, bins=4)
    assert p.bin_volumes == (10, 50, 30, 10)
    assert (p.poc, p.val, p.vah) == (2.5, 2, 4)
    assert p.actual_value_area_pct == 80
    assert sum(p.bin_volumes) == p.total_volume


def test_uniform_allocation_ties_and_full_area():
    p = compute_volume_profile([[1, 2, 5, 1, 4, 100]], bins=4)
    assert p.bin_volumes == (25, 25, 25, 25)
    assert (p.poc, p.val, p.vah) == (1.5, 1, 4)
    p = compute_volume_profile([[1, 2, 5, 1, 4, 100]], bins=4, value_area_pct=100)
    assert (p.val, p.vah) == (1, 5)


def test_flat_single_and_zero_volume():
    p = compute_volume_profile([[1,2,2,2,2,10]])
    assert (p.val, p.vah, p.poc, p.candle_count) == (2,2,2,1)
    with pytest.raises(ValueError):
        compute_volume_profile([[1,2,2,2,2,0]])


@pytest.mark.parametrize('rows', [[], [[1,2]], [[1,2,1,2,2,1]],
    [[1,2,2,2,2,-1]], [[1,2,2,2,2,float('nan')]],
    [[1,2,2,2,2,1], [1,2,2,2,2,1]]])
def test_invalid_rows_fail_loudly(rows):
    with pytest.raises(ValueError):
        compute_volume_profile(rows)


def test_contract_fixture_math():
    p = compute_volume_profile(CAPTURED['data']['attributes']['ohlcv_list'])
    assert p.candle_count == 12
    assert p.val <= p.poc <= p.vah
    assert p.actual_value_area_pct >= 70
    assert sum(p.bin_volumes) == pytest.approx(p.total_volume)


def fetch(handler, candidate=None, config=None, retries=0, at=None, twice=False, sleep=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            f = VolumeProfileFetcher(client=client, retries=retries,
                                     config=config or VolumeProfileSettings())
            if sleep is not None:
                f.sleep = sleep
            result = await f.profile(candidate or pool(), at=at or AT)
            if twice:
                assert await f.profile(candidate or pool(), at=at or AT) == result
            return result
    return asyncio.run(run())


def candles(interval, count=12):
    end = int(AT.timestamp()) // (interval * 60) * interval * 60
    return {'data': {'attributes': {'ohlcv_list': [
        [end - (i+1)*interval*60,1.8,1.9,1.7,1.8,100] for i in range(count)]}}}


def test_fetch_contract_and_cache():
    calls = []
    def handler(r):
        calls.append(r)
        assert r.url.path.endswith('/ohlcv/minute')
        assert r.url.params['aggregate'] == '15'
        assert r.url.params['currency'] == 'usd' and r.url.params['token'] == 'base'
        return httpx.Response(200, json=CAPTURED)
    at = datetime.fromtimestamp(1794232800, timezone.utc)
    result = fetch(handler, pool(first_seen_at=at-timedelta(hours=30)), at=at, twice=True)
    assert result['status'] == 'available' and result['candle_count'] == 12
    assert result['timeframe'] == '15m' and len(calls) == 1


@pytest.mark.parametrize('count', [0, 2])
def test_short_15m_falls_back_5m(count):
    calls = []
    def handler(r):
        n = int(r.url.params['aggregate'])
        calls.append(n)
        return httpx.Response(200, json=candles(n, count if n == 15 else 12))
    result = fetch(handler, pool(first_seen_at=AT-timedelta(hours=30)))
    assert calls == [15, 5] and result['status'] == 'available'
    assert result['timeframe'] == '5m'


def test_young_pool_5m_and_insufficient_cached():
    calls = []
    def handler(r):
        calls.append(r.url.params['aggregate'])
        return httpx.Response(200, json=candles(5, 2))
    result = fetch(handler, twice=True)
    assert calls == ['5'] and result['status'] == 'unavailable'
    assert 'insufficient' in result['reason']
    assert 'too few' in result['attempts'][0]


@pytest.mark.parametrize('kind', ['429', 'missing', 'stale'])
def test_unavailable_never_buys(kind):
    def handler(r):
        if kind == '429':
            return httpx.Response(429)
        if kind == 'missing':
            return httpx.Response(200, json={'data': {}})
        return httpx.Response(200, json={'data': {'attributes': {'ohlcv_list': [
            [100+i*300,1.8,1.9,1.7,1.8,100] for i in range(12)]}}})
    vp = fetch(handler, twice=True)
    assert vp['status'] == 'unavailable' and vp['reason']
    d = evaluate(pool(), security(), cycle_id='offline', account=account(), at=AT, volume_profile=vp)
    assert d.decision == 'REJECT' and 'unavailable' in d.reasoning


@pytest.mark.parametrize('price,expected', [(2,'BUY'), (1.8,'REJECT'), (1.6,'REJECT')])
def test_acceptance_invalidation_and_stops(price, expected):
    vp = dict(asdict(compute_volume_profile([[1,1.8,1.9,1.7,1.8,100]], bins=1)), status='available')
    d = evaluate(pool(price_usd=price), security(), cycle_id='offline', account=account(), at=AT, volume_profile=vp)
    assert d.decision == expected
    assert d.proposal['volume_profile']['price_vs_value_area'] == price_position(price, vp)
    if expected == 'BUY':
        assert d.proposal['stop_loss'] == pytest.approx(1.7)
    elif price < 1.7:
        assert 'invalidation' in d.reasoning


def test_val_stop_cap_and_cost_risk():
    assert profile_stop(2, 1.8, .15) == (1.8, 'Stop at VAL')
    stop, reason = profile_stop(2, 1.5, .15)
    assert stop == 1.7 and 'fraction cap' in reason
    d = evaluate(pool(), security(), cycle_id='offline', account=account(), at=AT,
                 volume_profile={'status':'available','val':1.5,'vah':1.9,'poc':1.7})
    assert d.proposal['stop_loss'] == 1.7
    assert 'fraction cap' in d.proposal['stop_basis']
    assert d.proposal['max_estimated_loss'] <= 100


def test_missing_profile_and_required_settings(monkeypatch):
    d = evaluate(pool(), security(), cycle_id='offline', account=account(), at=AT)
    assert d.decision == 'REJECT' and d.proposal['volume_profile']['status'] == 'unavailable'
    monkeypatch.setenv('VOLUME_PROFILE_REQUIRED_FOR_BUY', 'true')
    assert VolumeProfileSettings().required_for_buy
    monkeypatch.setenv('VOLUME_PROFILE_REQUIRED_FOR_BUY', 'false')
    with pytest.raises(ValueError):
        VolumeProfileSettings()


def test_hard_filter_preflight_no_http(tmp_path):
    async def run():
        def fail(r):
            raise AssertionError('Hard-filter reject must not fetch OHLCV')
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            return await collect_volume_profiles([pool(liquidity_usd=1)], {'mint':security()},
                PaperLedger(tmp_path/'ledger.json'), Settings(_env_file=None), client=client, at=AT)
    assert asyncio.run(run()) == {}


def test_429_retry_after_then_success():
    calls, delays = [], []
    async def sleep(delay):
        delays.append(delay)
    def handler(r):
        calls.append(r)
        if len(calls) == 1:
            return httpx.Response(429, headers={'Retry-After': '65'})
        return httpx.Response(200, json=candles(5))
    result = fetch(handler, retries=1, twice=True, sleep=sleep)
    assert result['status'] == 'available'
    assert delays == [65] and len(calls) == 2


def test_dashboard_profile_fields_and_escaping(monkeypatch):
    from act import api
    monkeypatch.setattr(api, 'LATEST', {'decisions': [
        {'mint_address': '<unsafe>', 'decision': 'BUY', 'proposal': {'volume_profile': {
            'status': 'available', 'poc': 1.8, 'vah': 1.9, 'val': 1.7,
            'price_vs_value_area': 'at_or_above_VAH', 'candle_count': 12,
            'timeframe': '5m', 'value_area_pct': 70}}},
        {'mint_address': 'missing', 'decision': 'REJECT', 'proposal': {'volume_profile': {
            'status': 'unavailable', 'reason': 'HTTP 429 exhausted'}}},
        {'mint_address': 'hardfail', 'decision': 'REJECT', 'proposal': None}]})
    html = api.dashboard()
    for text in ('SIMULATED - PAPER TRADING ONLY', 'POC USD', 'VAH USD', 'VAL USD',
                 'Completed candles', 'at_or_above_VAH', 'HTTP 429 exhausted',
                 'not_attempted', '&lt;unsafe&gt;'):
        assert text in html
    assert '<unsafe>' not in html
