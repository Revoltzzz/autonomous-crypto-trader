"""Solana pool discovery using the GeckoTerminal v2 JSON:API contract."""
import logging

import httpx

from act.collectors.base import BaseCollector, CollectorError, retry_delay
from act.domain.models import TokenDiscovery

URL = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
DISCOVERY_PAGE = "https://www.geckoterminal.com/explore/new-crypto-pools/solana"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "AutonomousCryptoTrader/1.0 (paper-only research)",
}
log = logging.getLogger(__name__)


def object_at(value, path):
    if not isinstance(value, dict):
        raise ValueError(f"Expected object at {path}; received {type(value).__name__}")
    return value


def string_at(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected nonempty string at {path}; received {type(value).__name__}")
    return value


class GeckoTerminalCollector(BaseCollector):
    async def _payload(self, *, url=URL, params=None):
        # Free API limits can be shared by an entire hosting egress IP. A burst
        # of 1/2/4-second retries does not let a minute-scale limit recover.
        # Request-local headers must not mutate a client shared with collectors.
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.get(
                    url, params=params if params is not None else {"include": "base_token"}, headers=HEADERS,
                    timeout=self.timeout,
                )
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise CollectorError(f"GeckoTerminal transport failure at {url}") from exc
                await self.sleep(2 ** attempt)
                continue
            status = response.status_code
            if status == 429 or status >= 500:
                if attempt == self.retries:
                    raise CollectorError(
                        f"GeckoTerminal HTTP {status} at {url}; retries exhausted; "
                        "no data fabricated"
                    )
                floor = 30 * (2 ** attempt) if status == 429 else 2 ** attempt
                delay = max(floor, retry_delay(response.headers.get("Retry-After"), 0))
                log.warning("GeckoTerminal HTTP %s; retry %s in %ss", status, attempt + 1, delay)
                await self.sleep(delay)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise CollectorError(f"GeckoTerminal HTTP {status} at {url}") from exc
            try:
                return response.json()
            except ValueError as exc:
                raise CollectorError(
                    f"GeckoTerminal expected JSON object at {url}; received invalid JSON"
                ) from exc
        raise CollectorError(f"GeckoTerminal no response at {url}")

    async def fetch(self) -> list[TokenDiscovery]:
        payload = await self._payload()
        path = "response"
        try:
            payload = object_at(payload, path)
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows:
                raise ValueError(
                    f"Expected nonempty data array; received {type(rows).__name__} "
                    f"with length {len(rows) if isinstance(rows, list) else 'unknown'}"
                )
            included_rows = payload.get("included", [])
            if not isinstance(included_rows, list):
                raise ValueError(f"Expected included array; received {type(included_rows).__name__}")
            included = {}
            for i, item in enumerate(included_rows):
                path = f"included[{i}]"
                item = object_at(item, path)
                if item.get("type") == "token":
                    token_id = string_at(item.get("id"), f"{path}.id")
                    included[token_id] = object_at(item.get("attributes"), f"{path}.attributes")
            result = []
            for i, row in enumerate(rows):
                path = f"data[{i}]"
                row = object_at(row, path)
                if row.get("type") != "pool":
                    raise ValueError(f"Expected pool type at {path}; received {row.get('type')!r}")
                attrs = object_at(row.get("attributes"), f"{path}.attributes")
                relationships = object_at(row.get("relationships"), f"{path}.relationships")
                base = object_at(relationships.get("base_token"), f"{path}.relationships.base_token")
                base = object_at(base.get("data"), f"{path}.relationships.base_token.data")
                token_id = string_at(base.get("id"), f"{path}.relationships.base_token.data.id")
                if not token_id.startswith("solana_") or not token_id.removeprefix("solana_"):
                    raise ValueError(f"Expected solana_<mint> base token id at {path}")
                mint = token_id.removeprefix("solana_")
                pool = string_at(attrs.get("address"), f"{path}.attributes.address")
                created = string_at(attrs.get("pool_created_at"), f"{path}.attributes.pool_created_at")
                token = included.get(token_id, {})
                volume = object_at(attrs.get("volume_usd", {}), f"{path}.attributes.volume_usd")
                tx = object_at(attrs.get("transactions", {}), f"{path}.attributes.transactions")
                tx = object_at(tx.get("h24", {}), f"{path}.attributes.transactions.h24")
                fields = {
                    "name": token.get("name"),
                    "symbol": token.get("symbol"),
                    "price_usd": attrs.get("base_token_price_usd"),
                    "liquidity_usd": attrs.get("reserve_in_usd"),
                    "volume_24h_usd": volume.get("h24"),
                    "buys_24h": tx.get("buys"),
                    "sells_24h": tx.get("sells"),
                }
                fields = {key: None if value == "" else value for key, value in fields.items()}
                # Do not infer a token name/symbol by splitting a pool pair name.
                result.append(TokenDiscovery(
                    **fields, mint_address=mint, pool_address=pool,
                    first_seen_at=created,
                    source_url=f"https://www.geckoterminal.com/solana/pools/{pool}",
                    unverified_fields=[key for key, value in fields.items() if value is None],
                ))
            return result
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise CollectorError(
                f"GeckoTerminal response contract error at {path}: {exc}; "
                f"expected v2 pool data and base_token relationships at {URL}; no data fabricated"
            ) from exc
