"""Allowlisted, keyless Binance reads. No account, signed or order interface."""
import asyncio
from dataclasses import dataclass
import math
import os
from typing import Literal
from urllib.parse import urlencode

import httpx

from act.agents import utcnow

Market = Literal["spot", "futures"]
BASES = {"spot": "https://api.binance.com/api/v3/",
         "futures": "https://fapi.binance.com/fapi/v1/"}
# Exact origins only: no credentials, ports, paths, queries, mirrors or proxies.
OFFICIAL_BASES = {
    "spot": {"https://api.binance.com", "https://data-api.binance.vision"},
    "futures": {"https://fapi.binance.com"},
}


def configured_base(market):
    default = BASES[market].split("/api/")[0].split("/fapi/")[0]
    base = os.environ.get(f"BINANCE_{market.upper()}_BASE_URL", default)
    if base not in OFFICIAL_BASES[market]:
        raise ValueError(f"Invalid BINANCE_{market.upper()}_BASE_URL: official origin required")
    return base


WEIGHTS = {"exchangeInfo": 20, "ticker/24hr": 80, "klines": 2, "depth": 5,
           "premiumIndex": 10, "fundingRate": 1, "openInterest": 1}
FUTURES_ONLY = {"premiumIndex", "fundingRate", "openInterest"}


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
    def __init__(self, context, market: Market = "futures", *, weight_limit=200,
                 retries=1, sleep=asyncio.sleep):
        if market not in BASES:
            raise ValueError("Unknown Binance market")
        self.base_url = configured_base(market)
        self.market = market
        self.context = context
        self.weight_limit = weight_limit
        self.weight_used = 0
        self.header_weight = 0
        self.retries = min(2, max(0, retries))
        self.sleep = sleep

    def weight(self, endpoint, params):
        if self.market == "futures":
            if endpoint == "exchangeInfo":
                return 1
            if endpoint == "ticker/24hr":
                return 40
        if endpoint == "premiumIndex" and params.get("symbol"):
            return 1
        return WEIGHTS[endpoint]

    async def read(self, endpoint: str, params: dict | None = None) -> Response:
        if endpoint not in WEIGHTS or (self.market == "spot" and endpoint in FUTURES_ONLY):
            raise ValueError("Only allowlisted public market-data endpoints for this market")
        params = dict(params or {})
        allowed = {"klines": {"symbol", "interval", "limit"}, "depth": {"symbol", "limit"},
                   "fundingRate": {"symbol", "limit"}, "premiumIndex": {"symbol"},
                   "openInterest": {"symbol"}}
        if set(params) - allowed.get(endpoint, set()):
            raise ValueError("Unsupported public data parameter")
        if endpoint in ("klines", "depth", "fundingRate", "openInterest"):
            symbol = params.get("symbol")
            if not isinstance(symbol, str) or not symbol.isalnum() or not symbol.isascii():
                raise ValueError("Public instrument symbol required")
        if endpoint == "depth" and params.get("limit") != 20:
            raise ValueError("Depth snapshot limit must be 20")
        if endpoint == "klines" and (params.get("interval") not in ("1m", "5m", "15m", "1h", "4h")
                                     or params.get("limit") != 60):
            raise ValueError("Klines require supported interval and limit=60")
        if endpoint == "fundingRate" and params.get("limit") != 100:
            raise ValueError("Funding history limit must be 100")
        prefix = "/api/v3/" if self.market == "spot" else "/fapi/v1/"
        url = self.base_url + prefix + endpoint
        for attempt in range(self.retries + 1):
            weight = self.weight(endpoint, params)
            if self.weight_used + weight > self.weight_limit or self.header_weight + weight > 2000:
                raise RateLimited("Binance conservative request-weight budget exhausted")
            self.weight_used += weight
            request_url = url + ("?" + urlencode(params) if params else "")
            provenance_start = len(self.context.provenance)
            try:
                response = await self.context.fetch(request_url)
            except httpx.HTTPStatusError as exc:
                response = exc.response
            except (httpx.RequestError, TimeoutError) as exc:
                raise NetworkError(type(exc).__name__) from exc
            except ValueError as exc:
                raise Malformed(str(exc)) from exc
            finally:
                for entry in self.context.provenance[provenance_start:]:
                    entry["base_url"] = self.base_url
            source = {"base_url": self.base_url, "market": self.market, "source_url": url, "params": dict(params),
                      "fetched_at": utcnow(), "http_status": response.status_code,
                      "weight_used": self.weight_used}
            self.context.provenance.append(source)
            try:
                observed_weight = int(response.headers.get("x-mbx-used-weight-1m", "0"))
                if observed_weight < 0:
                    raise ValueError
                self.header_weight = max(self.header_weight, observed_weight)
            except ValueError:
                raise Malformed("Invalid Binance weight header") from None
            source["observed_weight_1m"] = self.header_weight
            status = response.status_code
            if status in (403, 451):
                raise GeoBlocked(f"Binance {self.market} access denied HTTP {status}; no substitute exchange")
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

    async def premium_index(self, symbol):
        return await self.read("premiumIndex", {"symbol": symbol})

    async def open_interest(self, symbol):
        return await self.read("openInterest", {"symbol": symbol})

    async def funding_rate(self, symbol):
        return await self.read("fundingRate", {"symbol": symbol, "limit": 100})
