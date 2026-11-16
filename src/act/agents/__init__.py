"""Bounded, read-only research agents. No execution or strategy interface."""
import asyncio
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
            response = await asyncio.wait_for(
                self._client.get(url, timeout=min(4, self.remaining), follow_redirects=False),
                timeout=self.remaining)
            source['http_status'] = response.status_code
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError('Feed exceeds 2MB limit')
            source['status'] = 'success'
            return response
        except Exception:
            source['status'] = 'failed'
            raise


async def run_agents(report, *, client=None, registry=None):
    if registry is None:
        from act.agents.news import NewsAgent
        registry = Registry()
        registry.register(NewsAgent())
    snapshot = freeze(deepcopy(report))
    results = {}
    async with httpx.AsyncClient(headers={'User-Agent': 'ACT-PaperResearch/1.0'}) as owned:
        for agent in registry.all():
            started = utcnow()
            context = None
            try:
                seconds = budget(agent.name, 'TIME_BUDGET_SECONDS', 12)
                fetches = int(budget(agent.name, 'FETCH_BUDGET', 6))
                context = Context(snapshot, client if client is not None else owned, seconds, fetches)
                if seconds == 0:
                    raise BudgetExceeded('Agent time budget is zero')
                data = await asyncio.wait_for(agent.run(context), timeout=seconds)
                if not isinstance(data, dict):
                    raise TypeError('Agent must return structured dictionary')
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
