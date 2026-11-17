"""Keyless Binance USDT-M reads only. No configurable host or write interface."""
import asyncio
from dataclasses import dataclass
import math
from urllib.parse import urlencode

import httpx

from act.agents import BudgetExceeded, utcnow

BASE = "https://fapi.binance.com/fapi/v1/"
# Conservative weights for the fixed parameters below; headers may raise usage.
WEIGHTS = {"exchangeInfo": 1, "ticker/24hr": 40, "klines": 2,
           "depth": 5, "premiumIndex": 10, "fundingRate": 1}


class BinanceError(RuntimeError):
    """Public data unavailable; never replace with another exchange."""


class NetworkError(BinanceError):
    pass


class RateLimited(BinanceError):
    pass


class GeoBlocked(BinanceError):
    pass


class Malformed(BinanceError):
    pass


@dataclass(frozen=True)
class Response:
    data: dict | list
    provenance: dict


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise Malformed("Expected finite numeric market data") from None
    if not math.isfinite(result):
        raise Malformed("Non-finite market data")
    return result


class BinanceData:
    def __init__(self, context, *, weight_limit=100, retries=1, sleep=asyncio.sleep):
        self.context = context
        self.weight_limit = weight_limit
        self.weight_used = 0
        self.header_weight = 0
        self.retries = min(2, max(0, retries))
        self.sleep = sleep

    async def read(self, endpoint: str, params: dict | None = None) -> Response:
        if endpoint not in WEIGHTS:
            raise ValueError("Only allowlisted public market-data endpoints")
        params = dict(params or {})
        allowed = {"klines": {"symbol", "interval", "limit"},
                   "depth": {"symbol", "limit"}, "fundingRate": {"symbol", "limit"}}
        if set(params) - allowed.get(endpoint, set()):
            raise ValueError("Unsupported public data parameter")
        if endpoint == "depth" and params.get("limit") != 20:
            raise ValueError("Depth snapshot limit must be 20")
        if endpoint == "klines" and (params.get("interval") not in ("1m", "5m", "15m")
                                     or params.get("limit") != 60):
            raise ValueError("Klines require supported interval and limit=60")
        if endpoint == "fundingRate" and params.get("limit") != 100:
            raise ValueError("Funding history limit must be 100")
        url = BASE + endpoint
        for attempt in range(self.retries + 1):
            weight = WEIGHTS[endpoint]
            if self.weight_used + weight > self.weight_limit or self.header_weight + weight > 2000:
                raise RateLimited("Binance conservative request-weight budget exhausted")
            self.weight_used += weight
            request_url = url + ("?" + urlencode(params) if params else "")
            try:
                # Context owns per-attempt timeout, fetch budget and raw provenance.
                response = await self.context.fetch(request_url)
            except httpx.HTTPStatusError as exc:
                response = exc.response
            except (httpx.RequestError, TimeoutError) as exc:
                raise NetworkError(type(exc).__name__) from exc
            except BudgetExceeded:
                raise
            try:
                self.header_weight = max(self.header_weight, int(
                    response.headers.get("x-mbx-used-weight-1m", "0")))
            except ValueError:
                raise Malformed("Invalid Binance weight header") from None
            status = response.status_code
            source = {"source_url": url, "params": params, "fetched_at": utcnow(),
                      "http_status": status, "weight_used": self.weight_used,
                      "observed_weight_1m": self.header_weight}
            self.context.provenance.append(source)
            if status in (403, 451):
                raise GeoBlocked(f"Binance access denied HTTP {status}; deploy region may be restricted")
            if status == 418:
                raise RateLimited("Binance IP ban HTTP 418; stop requests")
            if status == 429 or status >= 500:
                error = RateLimited if status == 429 else NetworkError
                if attempt == self.retries:
                    raise error(f"Binance HTTP {status}; retry allowance exhausted")
                try:
                    delay = max(2 ** attempt, float(response.headers.get("retry-after", "1")))
                    if not math.isfinite(delay):
                        raise ValueError
                except ValueError:
                    raise Malformed("Invalid Retry-After") from None
                if delay >= self.context.remaining:
                    raise error(f"Binance HTTP {status}; backoff exceeds remaining time")
                await self.sleep(delay)
                continue
            if status != 200:
                raise Malformed(f"Unexpected Binance HTTP {status}")
            try:
                data = response.json()
                if not isinstance(data, (dict, list)) or (isinstance(data, dict) and "code" in data):
                    raise ValueError
            except ValueError:
                raise Malformed("Invalid Binance public response") from None
            return Response(data, source)
        raise NetworkError("No response")

    async def exchange_info(self):
        return await self.read("exchangeInfo")

    async def tickers(self):
        return await self.read("ticker/24hr")

    async def depth(self, symbol):
        return await self.read("depth", {"symbol": symbol, "limit": 20})

    async def klines(self, symbol, interval):
        return await self.read("klines", {"symbol": symbol, "interval": interval, "limit": 60})

    async def premium_index(self):
        return await self.read("premiumIndex")

    async def funding_rate(self, symbol):
        return await self.read("fundingRate", {"symbol": symbol, "limit": 100})
