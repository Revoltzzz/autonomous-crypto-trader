"""Free feeds, conservative evidence labels and deterministic relevance (not sentiment)."""
import asyncio
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from html import escape
import logging
import re
from time import monotonic
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from xml.etree import ElementTree as ET

from act.agents import BudgetExceeded, utcnow

LOG = logging.getLogger(__name__)
# Explicit editorial groups: additional feeds from an outlet never count as another outlet.
FEEDS = (
    ('CoinDesk', 'coindesk', 'https://www.coindesk.com/arc/outboundfeeds/rss/', False),
    ('Cointelegraph', 'cointelegraph', 'https://cointelegraph.com/rss', False),
    ('The Block', 'theblock', 'https://www.theblock.co/rss.xml', False),
    ('Decrypt', 'decrypt', 'https://decrypt.co/feed', False),
    ('Coinbase', 'coinbase', 'https://www.coinbase.com/blog/rss.xml', True),
    ('Federal Reserve', 'federalreserve', 'https://www.federalreserve.gov/feeds/press_all.xml', True),
)
NEXT_FETCH = {}


def canonical(url):
    p = urlsplit(url.strip())
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        return None
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(query), ''))


def normalized(title):
    return ' '.join(re.findall(r"[a-z0-9]+", title.casefold()))


def classify(title):
    rules = (
        ('regulatory', ('sec', 'regulator', 'lawsuit', 'court', 'cftc', 'legislation')),
        ('macro', ('fed', 'federal reserve', 'inflation', 'interest rate', 'cpi', 'jobs', 'treasury')),
        ('exchange', ('exchange', 'coinbase', 'binance', 'kraken', 'listing', 'delist')),
        ('solana-ecosystem', ('solana', 'sol', 'jupiter', 'raydium')),
    )
    words = ' ' + normalized(title) + ' '
    for category, terms in rules:
        hits = [term for term in terms if ' ' + term + ' ' in words]
        if hits:
            return category, hits
    return 'token-specific', []


def score(title):
    category, hits = classify(title)
    base = {'macro': 4, 'regulatory': 4, 'exchange': 3, 'solana-ecosystem': 3,
            'token-specific': 1}[category]
    reasons = [f'{category} scope: {base} relevance points']
    if hits:
        reasons.append('Headline keywords: ' + ', '.join(hits))
    else:
        reasons.append('Fallback topic bucket; token identity not inferred')
    return base, reasons


def child_text(node, names):
    for child in node:
        if child.tag.rsplit('}', 1)[-1] in names and child.text:
            return ' '.join(child.text.split())
    return ''


def parse_feed(content, feed, fetched_at):
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('DTD/entity feed rejected')
    root = ET.fromstring(content)
    if root.tag.rsplit('}', 1)[-1] not in ('rss', 'feed', 'RDF'):
        raise ValueError('Not an RSS/Atom feed')
    name, group, feed_url, official = feed
    items = []
    for node in root.iter():
        if node.tag.rsplit('}', 1)[-1] not in ('item', 'entry'):
            continue
        title = child_text(node, {'title'})[:500]
        url = child_text(node, {'link'})
        for link in node:
            if link.tag.rsplit('}', 1)[-1] == 'link' and link.get('rel', 'alternate') == 'alternate':
                url = link.get('href') or url
        url = canonical(url)
        if not title or not url:
            continue
        raw_date = child_text(node, {'pubDate', 'published', 'updated'})
        published = None
        try:
            dt = parsedate_to_datetime(raw_date) if ',' in raw_date else datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
            if dt.tzinfo:
                published = dt.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            pass
        # A primary label is allowed only on the configured official site's own links.
        host = urlsplit(url).hostname
        domain = urlsplit(feed_url).hostname.removeprefix('www.')
        primary = official and (host == domain or host.endswith('.' + domain))
        summary = child_text(node, {'description', 'summary', 'author', 'creator'})
        syndicated = bool(re.search(r'reuters|associated press|syndicat|press release|business wire|pr newswire',
                                    title + ' ' + summary, re.I))
        items.append({'title': title, 'url': url, 'published_at': published,
                      'sources': [{'outlet': name, 'editorial_group': group, 'url': url,
                                   'feed_url': feed_url, 'fetched_at': fetched_at,
                                   'primary': bool(primary), 'syndicated': syndicated}]})
        if len(items) >= 50:
            break
    return items


def same_event(a, b):
    # Deliberately high precision/low recall: fuzzy grouping is NOT corroboration.
    key = normalized(a['title'])
    if key != normalized(b['title']) or len(key.split()) < 6:
        return False
    if not a.get('published_at') or not b.get('published_at'):
        return False
    return abs((datetime.fromisoformat(a['published_at']) -
                datetime.fromisoformat(b['published_at'])).total_seconds()) <= 86400


def build_items(rows):
    groups = []
    for row in rows:
        title = normalized(row['title'])
        target = next((g for g in groups if any(
            r['url'] == row['url'] or
            (len(title.split()) >= 6 and SequenceMatcher(None, normalized(r['title']), title).ratio() >= .92)
            for r in g)), None)
        if target is None:
            groups.append([row])
        else:
            target.append(row)
    result = []
    for group in groups:
        first = group[0]
        sources = list({(s['editorial_group'], s['url']): s
                        for row in group for s in row['sources']}.values())
        primary = [s for s in sources if s['primary']]
        evidence = None
        # Only exact normalized, dated reports with different URLs/editorial groups qualify.
        for a in group:
            for b in group:
                sa, sb = a['sources'][0], b['sources'][0]
                if (a['url'] != b['url'] and same_event(a, b) and
                    sa['editorial_group'] != sb['editorial_group'] and
                    not sa['syndicated'] and not sb['syndicated']):
                    evidence = [sa['url'], sb['url']]
        # A near-title primary source does NOT confirm the representative item's event.
        exact_primary = [s for row in group if normalized(row['title']) == normalized(first['title'])
                         for s in row['sources'] if s in primary]
        representative_evidence = evidence if evidence and any(
            same_event(first, row) for row in group if row['url'] in evidence) else None
        verified = bool(exact_primary or representative_evidence)
        points, reasons = score(first['title'])
        result.append(dict(first, sources=sources, verification='verified' if verified else 'unverified',
                           verification_reason=('Official primary source on its own domain' if exact_primary else
                             'Exact normalized headline within 24h from two editorial groups; no syndication marker detected'
                             if representative_evidence else 'Insufficient independent confirmation'),
                           verification_urls=[s['url'] for s in exact_primary] or representative_evidence or [],
                           category=classify(first['title'])[0], relevance_score=points,
                           relevance_reasons=reasons,
                           related_headlines=list(dict.fromkeys(r['title'] for r in group))))
    return sorted(result, key=lambda item: (item['verification'] == 'verified', item['relevance_score']), reverse=True)


class NewsAgent:
    name = 'news'

    def describe(self):
        return 'Keyless public RSS/Atom headlines; conservative verification; no trade signals.'

    async def run(self, context):
        rows, errors, skipped = [], [], []
        for feed in FEEDS:
            url = feed[2]
            if monotonic() < NEXT_FETCH.get(url, 0):
                skipped.append({'url': url, 'reason': 'Feed cooldown (including Retry-After)'})
                continue
            try:
                response = await context.fetch(url)
                rows.extend(parse_feed(response.content, feed, utcnow()))
                NEXT_FETCH[url] = monotonic() + 60
            except BudgetExceeded:
                skipped.append({'url': url, 'reason': 'Fetch/time budget exhausted'})
                break
            except Exception as exc:
                delay = 60
                response = getattr(exc, 'response', None)
                if response is not None and response.status_code == 429:
                    value = response.headers.get('Retry-After', '60')
                    try:
                        delay = max(60, float(value))
                    except ValueError:
                        try:
                            delay = max(60, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            delay = 300
                NEXT_FETCH[url] = monotonic() + min(delay, 86400)
                LOG.exception('NEWS_FEED_FAILED source=%s', url)
                errors.append({'source_url': url, 'error': type(exc).__name__})
            await asyncio.sleep(min(.1, context.remaining))
        items = build_items(rows)
        if not items and errors:
            raise RuntimeError('All available news fetches failed; see feed tracebacks')
        return {'as_of': utcnow(), 'items': items, 'errors': errors, 'skipped': skipped,
                'status': 'degraded' if errors or skipped else 'success'}


def news_brief(agents):
    result = agents.get('news') or {}
    data = result.get('data') or {}
    items = data.get('items') or []
    verified = [item for item in items if item['verification'] == 'verified']
    return {'as_of': data.get('as_of'), 'status': result.get('status', 'waiting'),
            'source_status': data.get('status'), 'total': len(items), 'verified': len(verified),
            'unverified': len(items) - len(verified), 'top_verified': verified[:5],
            'errors': data.get('errors', []), 'skipped': data.get('skipped', [])}


def news_panel(report):
    agents = report.get('agents') or {}
    brief = report.get('news_brief') or news_brief(agents)
    out = '<section><h2>// NEWS (research only)</h2>'
    if not brief.get('as_of'):
        return out + '<p>No news snapshot available yet. Status: ' + escape(str(brief.get('status'))) + '</p></section>'
    out += '<p>As of ' + escape(brief['as_of']) + ' | ' + escape(str(brief['verified'])) + ' verified / ' + escape(str(brief['total'])) + ' total. Source status: ' + escape(str(brief.get('source_status'))) + '</p>'
    items = ((agents.get('news') or {}).get('data') or {}).get('items') or []
    if not items:
        out += '<p>No items returned; no substitute headlines.</p>'
    for item in items:
        out += '<article><h3>' + escape(item['title']) + '</h3><p>' + escape(item['verification'] + ': ' + item['verification_reason']) + '</p>'
        out += '<p>' + escape(item['category']) + ' | relevance ' + escape(str(item['relevance_score'])) + ': ' + escape('; '.join(item['relevance_reasons'])) + '</p>'
        for source in item['sources']:
            url = canonical(source['url'])
            if url:
                out += '<p><a rel="noopener noreferrer" href="' + escape(url, quote=True) + '">' + escape(source['outlet']) + '</a> fetched ' + escape(source['fetched_at']) + '</p>'
        out += '</article>'
    return out + '</section>'
