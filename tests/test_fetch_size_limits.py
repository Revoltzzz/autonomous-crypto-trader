"""Offline byte-budget regressions; no exchange calls."""
import asyncio
import gzip

import httpx
import pytest

from act.agents import Context, spot_response_limit
from act.binance_data import BinanceData, Malformed

SPOT = 'https://data-api.binance.vision/api/v3/'


def fetch(url, body, *, headers=None, status=200):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, content=body, headers=headers)
        )) as client:
            context = Context({}, client, 60, 6)
            return await context.fetch(url), context
    return asyncio.run(run())


def test_defaults(monkeypatch):
    monkeypatch.delenv('BINANCE_SPOT_EXCHANGE_INFO_MAX_BYTES', raising=False)
    monkeypatch.delenv('BINANCE_SPOT_TICKER_MAX_BYTES', raising=False)
    assert spot_response_limit(SPOT + 'exchangeInfo') == 24_000_000
    assert spot_response_limit(SPOT + 'ticker/24hr') == 4_000_000


@pytest.mark.parametrize('url', [
    'https://news.example/rss',
    'https://fapi.binance.com/fapi/v1/exchangeInfo',
    SPOT + 'klines?symbol=BTCUSDT',
    'https://data-api.binance.vision.evil.test/api/v3/exchangeInfo',
    'https://data-api.binance.vision:443/api/v3/exchangeInfo',
    SPOT + 'exchangeInfo?symbol=BTCUSDT',
])
def test_other_paths_keep_two_mb(url):
    assert spot_response_limit(url) is None
    response, _ = fetch(url, b'x' * 2_000_000)
    assert len(response.content) == 2_000_000
    with pytest.raises(ValueError, match='2MB'):
        fetch(url, b'x' * 2_000_001)


@pytest.mark.parametrize('endpoint,size', [('exchangeInfo', 17_818_840), ('ticker/24hr', 3_000_000)])
def test_bulk_payload_passes(endpoint, size):
    # Synthetic JSON at the observed exchangeInfo decoded size, not a schema fixture.
    body = b'{"padding":"' + b'x' * (size - 14) + b'"}'
    response, context = fetch(SPOT + endpoint, body)
    assert len(response.content) == size
    assert len(response.json()['padding']) == size - 14
    assert context.provenance[0]['http_status'] == 200
    assert context.provenance[0]['status'] == 'success'


@pytest.mark.parametrize('endpoint,key', [
    ('exchangeInfo', 'BINANCE_SPOT_EXCHANGE_INFO_MAX_BYTES'),
    ('ticker/24hr', 'BINANCE_SPOT_TICKER_MAX_BYTES'),
])
def test_env_and_exact_boundary(monkeypatch, endpoint, key):
    monkeypatch.setenv(key, '100')
    assert len(fetch(SPOT + endpoint, b'x' * 100)[0].content) == 100
    with pytest.raises(ValueError, match='100 byte limit: reported 101 bytes'):
        fetch(SPOT + endpoint, b'x' * 101)


@pytest.mark.parametrize('value', ['0', '-1', '32000001', '1.5', 'nan', ''])
def test_invalid_overrides(monkeypatch, value):
    monkeypatch.setenv('BINANCE_SPOT_EXCHANGE_INFO_MAX_BYTES', value)
    with pytest.raises(ValueError, match='Invalid BINANCE_SPOT'):
        spot_response_limit(SPOT + 'exchangeInfo')


class Chunks(httpx.AsyncByteStream):
    def __init__(self):
        self.sent = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(100):
            self.sent += 1
            yield b'x' * 65_536

    async def aclose(self):
        self.closed = True


def test_chunked_oversize_is_malformed_and_closed(monkeypatch):
    monkeypatch.setenv('BINANCE_SPOT_BASE_URL', SPOT.split('/api/')[0])
    monkeypatch.setenv('BINANCE_SPOT_EXCHANGE_INFO_MAX_BYTES', '100000')
    async def run():
        stream = Chunks()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        )) as client:
            context = Context({}, client, 60, 6)
            with pytest.raises(Malformed, match='100000 byte limit: decoded at least 131072 bytes'):
                await BinanceData(context, 'spot').exchange_info()
            assert stream.sent == 2
            assert stream.closed
            source = context.provenance[0]
            assert source['status'] == 'failed'
            assert source['http_status'] == 200
            assert source['base_url'] == 'https://data-api.binance.vision'
    asyncio.run(run())


def test_compressed_decoded_limit(monkeypatch):
    monkeypatch.setenv('BINANCE_SPOT_TICKER_MAX_BYTES', '100000')
    body = gzip.compress(b'x' * 100001)
    with pytest.raises(ValueError, match='decoded at least 100001 bytes'):
        fetch(SPOT + 'ticker/24hr', body, headers={'content-encoding': 'gzip'})
    response, _ = fetch(SPOT + 'ticker/24hr', gzip.compress(b'[]'),
                        headers={'content-encoding': 'gzip'})
    assert response.json() == []


def test_redirect_cannot_inherit_bulk_budget():
    async def run():
        def handler(request):
            if request.url.host == 'data-api.binance.vision':
                return httpx.Response(302, headers={'location': 'https://news.example/rss'})
            return httpx.Response(200, content=b'x' * 2_000_001)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            context = Context({}, client, 60, 6)
            with pytest.raises(ValueError, match='2000000 byte limit: reported 2000001 bytes'):
                await context.fetch(SPOT + 'exchangeInfo')
    asyncio.run(run())


def test_futures_451_unchanged():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(451)
        )) as client:
            from act.binance_data import GeoBlocked
            context = Context({}, client, 60, 6)
            with pytest.raises(GeoBlocked, match='451'):
                await BinanceData(context, 'futures').exchange_info()
            assert context.provenance[0]['base_url'] == 'https://fapi.binance.com'
            assert context.provenance[0]['http_status'] == 451
    asyncio.run(run())
  
