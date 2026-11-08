"""Read-only HTTP collector with bounded retries and Retry-After support."""
import asyncio
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx


class CollectorError(RuntimeError):
    """Remote data could not be retrieved or validated."""


def retry_delay(header: str | None, fallback: float) -> float:
    if header is None:
        return fallback
    try:
        seconds = float(header)
        if not math.isfinite(seconds):
            raise ValueError("Non-finite Retry-After")
        return max(0, seconds)
    except ValueError:
        try:
            date = parsedate_to_datetime(header)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, (date - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return fallback


class BaseCollector:
    def __init__(self, client: httpx.AsyncClient | None = None,
                 timeout: float = 15, retries: int = 3, sleep=asyncio.sleep):
        if timeout <= 0 or retries < 0:
            raise ValueError("Invalid timeout or retry count")
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.owns_client = client is None
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self.owns_client:
            await self.client.aclose()

    async def get_json(self, url: str, params=None):
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.get(url, params=params, timeout=self.timeout)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise CollectorError(f"Transport failure at {url}") from exc
                await self.sleep(2 ** attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self.retries:
                    raise CollectorError(f"HTTP {response.status_code} at {url}; retries exhausted")
                delay = max(2 ** attempt, retry_delay(response.headers.get("Retry-After"), 0))
                await self.sleep(delay)
                continue
            try:
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, ValueError) as exc:
                raise CollectorError(f"HTTP or JSON error at {url}") from exc
        raise CollectorError(f"No response at {url}")
