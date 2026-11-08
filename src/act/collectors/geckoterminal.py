"""Solana pool discovery. Endpoint intentionally treated as unverified."""
from act.collectors.base import BaseCollector, CollectorError
from act.domain.models import TokenDiscovery

URL = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
DISCOVERY_PAGE = "https://www.geckoterminal.com/explore/new-crypto-pools/solana"


class GeckoTerminalCollector(BaseCollector):
    async def fetch(self) -> list[TokenDiscovery]:
        try:
            payload = await self.get_json(URL, params={"include": "base_token"})
            rows = payload["data"]
            if not isinstance(rows, list):
                raise ValueError("Expected data array")
            included = {item["id"]: item["attributes"]
                        for item in payload.get("included", [])}
            result = []
            for row in rows:
                attrs = row["attributes"]
                token_id = row["relationships"]["base_token"]["data"]["id"]
                if not token_id.startswith("solana_"):
                    raise ValueError("Unexpected token network")
                mint = token_id.removeprefix("solana_")
                pool = attrs["address"]
                if not mint or not pool:
                    raise ValueError("Missing token or pool address")
                token = included.get(token_id, {})
                volume = attrs.get("volume_usd") or {}
                transactions = (attrs.get("transactions") or {}).get("h24") or {}
                fields = {
                    "name": token.get("name"), "symbol": token.get("symbol"),
                    "price_usd": attrs.get("base_token_price_usd"),
                    "liquidity_usd": attrs.get("reserve_in_usd"),
                    "volume_24h_usd": volume.get("h24"),
                    "buys_24h": transactions.get("buys"),
                    "sells_24h": transactions.get("sells"),
                }
                fields = {key: None if value == "" else value for key, value in fields.items()}
                result.append(TokenDiscovery(
                    **fields, mint_address=mint, pool_address=pool,
                    first_seen_at=attrs["pool_created_at"],
                    source_url=f"https://www.geckoterminal.com/solana/pools/{pool}",
                    unverified_fields=[key for key, value in fields.items() if value is None],
                ))
            return result
        except (CollectorError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise CollectorError(
                f"GeckoTerminal endpoint needs re-verification: {URL}; "
                f"discovery page: {DISCOVERY_PAGE}; no data fabricated"
            ) from exc
