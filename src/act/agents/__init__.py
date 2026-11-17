"""Bounded, read-only research agents. No execution or strategy interface."""
import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
import os
from time import monotonic
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlsplit




import httpx




LOG = logging.getLogger(__name__)








def utcnow():
    return datetime.now(timezone.utc).isoformat()








def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value








def thaw(value):
    """Copy frozen snapshots into JSON-native values at result boundaries."""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(v) for v in value]
    return value


class Agent(Protocol):
    name: str




    def describe(self) -> str: ...
    async def run(self, context) -> dict: ...








class Registry:
    def __init__(self):
        self._agents = {}




    def register(self, agent: Agent):
        if not agent.name or not agent.name.replace('_', '').isalnum():
            raise ValueError('Agent names must be alphanumeric with optional underscores')
        if agent.name in self._agents:
            raise ValueError('Duplicate agent: ' + agent.name)
        self._agents[agent.name] = agent
        return agent




    def all(self):
        return tuple(self._agents.values())








class BudgetExceeded(RuntimeError):
    pass








def budget(name, kind, default):
    key = 'AGENT_' + name.upper() + '_' + kind
    value = float(os.getenv(key, os.getenv('AGENT_' + kind, str(default))))
    if not math.isfinite(value) or value < 0:
        raise ValueError('Invalid budget: ' + key)
    if kind == 'FETCH_BUDGET' and not value.is_integer():
        raise ValueError('Fetch budget must be an integer')
    return min(value, 60 if kind == 'TIME_BUDGET_SECONDS' else 20)








@dataclass
class Context:
    snapshot: object
    _client: object
    seconds: float
    fetch_limit: int
    provenance: list = field(default_factory=list)
    used: int = 0
    _started: float = field(default_factory=monotonic)




    @property
    def remaining(self):
        return max(0, self.seconds - (monotonic() - self._started))




    async def fetch(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Public HTTPS feed required')
        if self.used >= self.fetch_limit or self.remaining <= 0:
            raise BudgetExceeded('Agent fetch/time budget exhausted')
        self.used += 1
        source = {'source_url': url, 'fetched_at': utcnow(), 'status': 'pending'}
        self.provenance.append(source)
        try:
            limit = spot_response_limit(url)
            if limit is None:
                request = self._client.get(url, timeout=min(4, self.remaining), follow_redirects=True)
            else:
                request = read_spot_response(
                    self._client, url, min(4, self.remaining), limit, source)
            response = await asyncio.wait_for(request, timeout=self.remaining)
            source['fetched_at'] = utcnow()
            source['http_status'] = response.status_code
            response.raise_for_status()
            if limit is None and len(response.content) > 2_000_000:
                raise ValueError('Feed exceeds 2MB limit')
            source['status'] = 'success'
            return response
        except (Exception, asyncio.CancelledError):
            source['status'] = 'failed'
            raise









def spot_response_limit(url):
    """Only exact public Spot bulk URLs get a larger decoded-byte budget."""
    parsed = urlsplit(url)
    if parsed.netloc not in ('api.binance.com', 'data-api.binance.vision') or parsed.scheme != 'https':
        return None
    limits = {
        '/api/v3/exchangeInfo': ('BINANCE_SPOT_EXCHANGE_INFO_MAX_BYTES', 24_000_000),
        '/api/v3/ticker/24hr': ('BINANCE_SPOT_TICKER_MAX_BYTES', 4_000_000),
    }
    if parsed.path not in limits or parsed.query or parsed.fragment:
        return None
    key, default = limits[parsed.path]
    try:
        value = int(os.environ.get(key, str(default)))
    except ValueError:
        raise ValueError(f'Invalid {key}: integer bytes required') from None
    if not 1 <= value <= 32_000_000:
        raise ValueError(f'Invalid {key}: must be between 1 and 32000000 bytes')
    return value


async def read_spot_response(client, url, timeout, limit, source):
    """Bound retained decoded body before JSON parsing; no redirect cap escape."""
    async with client.stream('GET', url, timeout=timeout, follow_redirects=True) as response:
        source['fetched_at'] = utcnow()
        source['http_status'] = response.status_code
        response.raise_for_status()
        if spot_response_limit(str(response.url)) is None:
            limit = 2_000_000
        source['response_limit_bytes'] = limit
        reported = response.headers.get('content-length')
        source['reported_size_bytes'] = reported
        if reported is not None and reported.isdecimal() and int(reported) > limit:
            raise ValueError(f'Response exceeds {limit} byte limit: reported {reported} bytes')
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65_536):
            actual = len(body) + len(chunk)
            source['decoded_size_bytes'] = actual
            if actual > limit:
                raise ValueError(f'Response exceeds {limit} byte limit: decoded at least {actual} bytes'
                                 f' (reported {reported})')
            body.extend(chunk)
        # Body is already decoded. Avoid a second content-encoding decode.
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() not in ('content-encoding', 'content-length')}
        return httpx.Response(response.status_code, headers=headers, content=bytes(body),
                              request=response.request)

async def run_agents(report, *, client=None, registry=None):
    if registry is None:
        from act.agents.news import NewsAgent
        registry = Registry()
        registry.register(NewsAgent())
        from act.agents.scanner import MarketScannerAgent, TechnicalAnalysisAgent
        for market in ("spot", "futures"):
            registry.register(MarketScannerAgent(market))
        for market in ("spot", "futures"):
            registry.register(TechnicalAnalysisAgent(market))
    results = {}
    async with httpx.AsyncClient(headers={'User-Agent': 'ACT-PaperResearch/1.0'}) as owned:
        for agent in registry.all():
            started = utcnow()
            context = None
            try:
                seconds = budget(agent.name, 'TIME_BUDGET_SECONDS', 12)
                fetches = int(budget(agent.name, 'FETCH_BUDGET', 6))
                agent_snapshot = freeze({**deepcopy(report), "agents": deepcopy(results)})
                context = Context(agent_snapshot, client if client is not None else owned, seconds, fetches)
                if seconds == 0:
                    raise BudgetExceeded('Agent time budget is zero')
                data = await asyncio.wait_for(agent.run(context), timeout=seconds)
                if not isinstance(data, dict):
                    raise TypeError('Agent must return structured dictionary')
                data = thaw(data)
                json.dumps(data, allow_nan=False)
                result = {'status': 'success', 'data': data}
            except Exception as exc:
                LOG.exception('AGENT_FAILED name=%s cycle=%s', agent.name, report.get('cycle_id'))
                result = {'status': 'failed', 'error': {'type': type(exc).__name__, 'message': str(exc)}}
            result.update(name=agent.name, started_at=started, finished_at=utcnow(),
                          provenance=deepcopy(context.provenance) if context else [],
                          fetches_used=context.used if context else 0)
            results[agent.name] = result
    return results
    
