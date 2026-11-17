"""Offline news redirect/status regression fixtures."""
import asyncio

import httpx

from act.agents import Context
from act.agents import news


def test_public_feed_follows_redirect_and_records_status():
    calls = []
    def handle(request):
        calls.append(str(request.url))
        if request.url.path == "/old":
            return httpx.Response(308, headers={"location": "/rss"})
        return httpx.Response(200, text="<rss/>")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            context = Context({}, client, 12, 6)
            response = await context.fetch("https://fixture.test/old")
            assert response.status_code == 200
            assert context.provenance[0]["http_status"] == 200
    asyncio.run(run())
    assert calls == ["https://fixture.test/old", "https://fixture.test/rss"]


def test_news_errors_retain_http_status_for_http_and_parse_failures(monkeypatch):
    monkeypatch.setattr(news, "FEEDS", [("fixture", "fixture", "https://fixture.test/http"),
                                      ("fixture", "fixture", "https://fixture.test/html")])
    monkeypatch.setattr(news, "NEXT_FETCH", {})
    monkeypatch.setattr(news, "build_items", lambda rows: [{"fixture": True}])
    def handle(request):
        return httpx.Response(503 if request.url.path == "/http" else 200, text="<html>not RSS</html>")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await news.NewsAgent().run(Context({}, client, 12, 6))
            assert [r["http_status"] for r in result["errors"]] == [503, 200]
    asyncio.run(run())


def test_only_canonical_coindesk_and_no_coinbase():
    urls = [f[2] for f in news.FEEDS]
    assert "https://www.coindesk.com/arc/outboundfeeds/rss" in urls
    assert not any("coinbase" in u for u in urls)
