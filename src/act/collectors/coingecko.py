"""CoinGecko public simple-price collector. Missing metrics are errors."""
from datetime import datetime, timezone

from act.collectors.base import BaseCollector, CollectorError
from act.domain.models import MarketSnapshot

URL = "https://api.coingecko.com/api/v3/simple/price"
SYMBOLS = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}


class CoinGeckoCollector(BaseCollector):
    async def fetch(self, coin_ids=("bitcoin", "ethereum", "solana")):
        ids = list(coin_ids)
        if not ids:
            raise ValueError("At least one coin id is required")
        data = await self.get_json(URL, params={
            "ids": ",".join(ids), "vs_currencies": "usd",
            "include_market_cap": "true", "include_24hr_vol": "true",
            "include_24hr_change": "true",
        })
        now = datetime.now(timezone.utc)
        try:
            return [MarketSnapshot(
                symbol=SYMBOLS.get(coin, coin), price_usd=data[coin]["usd"],
                market_cap_usd=data[coin]["usd_market_cap"],
                volume_24h_usd=data[coin]["usd_24h_vol"],
                change_24h_pct=data[coin]["usd_24h_change"],
                fetched_at=now, source=URL,
            ) for coin in ids]
        except (KeyError, TypeError, ValueError) as exc:
            raise CollectorError("CoinGecko returned missing or invalid price metrics") from exc
