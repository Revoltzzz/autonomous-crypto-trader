"""Offline agent/news/Telegram regression fixtures; no production data asserted."""
import asyncio
from copy import deepcopy
import logging

import httpx
import pytest

from act.agents import BudgetExceeded, Context, Registry, freeze, run_agents
from act.agents.news import (FEEDS, NEXT_FETCH, NewsAgent, build_items, canonical,
                             classify, news_brief, news_panel, parse_feed, score)
from act.agents import telegram


class Good:
    name = 'good'

    def describe(self):
        return 'test fixture'

    async def run(self, context):
        return {'value': 1}


def row(title='Bitcoin market update reaches a new milestone today', outlet='A', url='https://a.test/item', primary=False, date='2026-11-17T00:00:00+00:00'):
    return {'title': title, 'url': url, 'published_at': date,
            'sources': [{'outlet': outlet, 'editorial_group': outlet, 'url': url,
                         'feed_url': 'https://a.test/rss', 'fetched_at': '2026-11-17T01:00:00+00:00',
                         'primary': primary, 'syndicated': False}]}


def test_registry():
    registry = Registry()
    good = registry.register(Good())
    assert registry.all() == (good,)
    with pytest.raises(ValueError, match='Duplicate'):
        registry.register(good)
    good.name = '../invalid'
    with pytest.raises(ValueError):
        Registry().register(good)


def test_isolation_and_immutable_snapshot(monkeypatch, caplog):
    class Bad(Good):
        name = 'bad'

        async def run(self, context):
            context.snapshot['nested']['list'][0]['value'] = 99

    monkeypatch.setenv('AGENT_TIME_BUDGET_SECONDS', '1')
    registry = Registry()
    registry.register(Bad())
    registry.register(Good())
    report = {'cycle_id': 'fixture', 'nested': {'list': [{'value': 1}]}}
    before = deepcopy(report)
    result = asyncio.run(run_agents(report, registry=registry))
    assert report == before
    assert result['bad']['status'] == 'failed'
    assert result['good']['data'] == {'value': 1}
    assert result['bad']['provenance'] == []
    assert any(r.exc_info for r in caplog.records if 'AGENT_FAILED' in r.message)


def test_time_budget(monkeypatch):
    class Slow(Good):
        name = 'slow'

        async def run(self, context):
            await asyncio.sleep(1)
            return {}

    monkeypatch.setenv('AGENT_SLOW_TIME_BUDGET_SECONDS', '.001')
    registry = Registry()
    registry.register(Slow())
    registry.register(Good())
    result = asyncio.run(run_agents({}, registry=registry))
    assert result['slow']['status'] == 'failed'
    assert result['good']['status'] == 'success'


@pytest.mark.parametrize('value', ['nan', '-1', 'inf', 'invalid'])
def test_invalid_budget_isolated(monkeypatch, value):
    monkeypatch.setenv('AGENT_GOOD_TIME_BUDGET_SECONDS', value)
    registry = Registry()
    registry.register(Good())
    assert asyncio.run(run_agents({}, registry=registry))['good']['status'] == 'failed'


def test_fetch_budget_and_provenance():
    async def run():
        calls = []
        def transport(request):
            calls.append(str(request.url))
            return httpx.Response(200, content=b'ok')
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            context = Context(freeze({}), client, 1, 1)
            await context.fetch('https://a.test/rss')
            with pytest.raises(BudgetExceeded):
                await context.fetch('https://a.test/rss')
            assert len(calls) == 1
            assert context.provenance[0]['source_url'] == calls[0]
            assert context.provenance[0]['fetched_at']
            assert context.provenance[0]['status'] == 'success'
    asyncio.run(run())


def test_failed_fetch_consumes_budget():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as client:
            ctx = Context(freeze({}), client, 1, 1)
            with pytest.raises(httpx.HTTPStatusError):
                await ctx.fetch('https://a.test/rss')
            assert ctx.used == 1
            assert ctx.provenance[0]['http_status'] == 429
            assert ctx.provenance[0]['status'] == 'failed'
    asyncio.run(run())


def test_zero_budget_no_network():
    async def run():
        ctx = Context(freeze({}), None, 1, 0)
        with pytest.raises(BudgetExceeded):
            await ctx.fetch('https://a.test/rss')
        assert not ctx.provenance
    asyncio.run(run())


def test_dedupe_url_and_near_title_without_verifying():
    a = row()
    b = row(title=a['title'] + ' now', outlet='B', url='https://b.test/item')
    items = build_items([a, deepcopy(a), b])
    assert len(items) == 1
    assert len(items[0]['sources']) == 2
    assert items[0]['verification'] == 'unverified'


def test_verification_two_independent_outlets():
    a = row()
    b = row(outlet='B', url='https://b.test/item')
    item = build_items([a, b])[0]
    assert item['verification'] == 'verified'
    assert len(item['verification_urls']) == 2


@pytest.mark.parametrize('change', ['same_group', 'same_url', 'syndicated', 'old', 'missing_date'])
def test_no_false_independence(change):
    a, b = row(), row(outlet='B', url='https://b.test/item')
    if change == 'same_group':
        b['sources'][0]['editorial_group'] = 'A'
    elif change == 'same_url':
        b['url'] = a['url']
        b['sources'][0]['url'] = a['url']
    elif change == 'syndicated':
        b['sources'][0]['syndicated'] = True
    elif change == 'old':
        b['published_at'] = '2026-11-14T00:00:00+00:00'
    else:
        b['published_at'] = None
    assert all(i['verification'] == 'unverified' for i in build_items([a, b]))


def test_primary_confirms_only_exact_headline():
    assert build_items([row(primary=True)])[0]['verification'] == 'verified'
    a, b = row(), row(title=row()['title'] + ' now', primary=True, url='https://b.test/item')
    assert build_items([a, b])[0]['verification'] == 'unverified'


@pytest.mark.parametrize('title,category', [
    ('SEC files lawsuit', 'regulatory'), ('Federal Reserve interest rate decision', 'macro'),
    ('Binance exchange listing', 'exchange'), ('Solana ecosystem update', 'solana-ecosystem'),
    ('Bitcoin update', 'token-specific')])
def test_classification_score(title, category):
    assert classify(title)[0] == category
    points, reasons = score(title)
    assert 1 <= points <= 4
    assert reasons
    assert 'sentiment' not in ' '.join(reasons)


def test_rss_atom_and_primary_domain_guard():
    feed = ('Official', 'official', 'https://official.test/rss', True)
    xml = b'<rss><channel><item><title>Fixture headline</title><link>https://evil.test/x</link></item></channel></rss>'
    assert parse_feed(xml, feed, 'now')[0]['sources'][0]['primary'] is False
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Fixture</title><link href="https://official.test/x"/><updated>2026-11-17T00:00:00Z</updated></entry></feed>'
    parsed = parse_feed(atom, feed, 'now')[0]
    assert parsed['sources'][0]['primary'] is True
    assert parsed['published_at']
    with pytest.raises(ValueError):
        parse_feed(b'<!DOCTYPE rss><rss/>', feed, 'now')
    with pytest.raises(ValueError):
        parse_feed(b'<html/>', feed, 'now')
    assert canonical('javascript:alert(1)') is None
    assert canonical('https://a.test/x?utm_source=z#fragment') == 'https://a.test/x'


def test_news_agent_mock_feed_and_brief():
    async def run():
        NEXT_FETCH.clear()
        xml = b'<rss><channel><item><title>Fixture macro news headline</title><link>https://a.test/x</link></item></channel></rss>'
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=xml))) as client:
            result = await NewsAgent().run(Context(freeze({}), client, 3, 6))
        assert len(result['items']) == 1
        assert len(result['items'][0]['sources']) == len(FEEDS)
        brief = news_brief({'news': {'status': 'success', 'data': result}})
        assert brief['total'] == 1 and brief['unverified'] == 1
        assert not brief['top_verified']
        NEXT_FETCH.clear()
    asyncio.run(run())


def test_rate_limit_cooldown():
    async def run():
        NEXT_FETCH.clear()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={'Retry-After': '120'}))) as client:
            with pytest.raises(RuntimeError):
                await NewsAgent().run(Context(freeze({}), client, 3, 6))
            assert len(NEXT_FETCH) == len(FEEDS)
            ctx = Context(freeze({}), client, 3, 6)
            result = await NewsAgent().run(ctx)
            assert ctx.used == 0 and len(result['skipped']) == len(FEEDS)
        NEXT_FETCH.clear()
    asyncio.run(run())


def test_panel_empty_escape_and_status():
    assert 'No news snapshot available yet' in news_panel({})
    item = build_items([row(title='<script>alert(1)</script>', primary=True)])[0]
    report = {'agents': {'news': {'status': 'success', 'data': {'as_of': 'fixture', 'items': [item]}}}}
    html = news_panel(report)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'As of fixture' in html
    assert 'verified' in html


def test_telegram_disabled_without_token(monkeypatch, caplog):
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'not-used')
    monkeypatch.setattr(telegram, '_DISABLED_LOGGED', False)
    with caplog.at_level(logging.INFO):
        assert asyncio.run(telegram.push_brief({}, client=object())) == {'status': 'disabled'}
        assert asyncio.run(telegram.push_brief({}, client=object())) == {'status': 'disabled'}
    assert sum('TELEGRAM_DISABLED' in r.message for r in caplog.records) == 1


def test_telegram_missing_chat_and_safe_failure(monkeypatch, caplog):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'offline-fixture-not-a-real-token')
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    assert asyncio.run(telegram.push_brief({}))['status'] == 'failed'
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'fixture')
    async def run():
        def fail(request):
            raise httpx.ConnectError('private-url:' + str(request.url), request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            assert (await telegram.push_brief({}, client=client))['status'] == 'failed'
    asyncio.run(run())
    assert 'offline-fixture-not-a-real-token' not in caplog.text


def test_telegram_only_verified_plaintext(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'offline-fixture-not-a-real-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'fixture')
    async def run():
        calls = []
        def respond(request):
            calls.append(request.content)
            return httpx.Response(200, json={'ok': True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            brief = news_brief({'news': {'status': 'success', 'data': {'items': build_items([row(primary=True), row(title='Different unconfirmed fixture headline', url='https://a.test/other')])}}})
            assert (await telegram.push_brief(brief, client=client))['status'] == 'sent'
        assert len(calls) == 1
        assert b'Different unconfirmed' not in calls[0]
        assert b'parse_mode' not in calls[0]
    asyncio.run(run())
