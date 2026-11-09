"""Cycle-observed paper position marks and risk-reducing simulated exits only."""
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from act.collectors.base import BaseCollector
from act.domain.models import RiskCheckResult
from act.paper.executor import PaperExecutor
from act.paper.ledger import positive

LOG = logging.getLogger(__name__)
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
CG = "https://api.coingecko.com/api/v3/simple/token_price/solana"


class PositionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSITION_", extra="ignore", frozen=True,
                                     allow_inf_nan=False)
    max_holding_hours: float = Field(default=48, gt=0)
    max_quote_age_seconds: float = Field(default=300, gt=0)


def price_value(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a quote")
    return positive(float(value))


class PositionQuoteCollector(BaseCollector):
    """Read-only public quote endpoints, using existing bounded retry/backoff."""

    async def fetch(self, position, config):
        errors = []
        try:
            if not position.pool_address:
                raise ValueError("Position has no recorded pool")
            payload = await self.get_json(GT + "/pools/" + quote(position.pool_address, safe=""))
            row = payload["data"]
            attrs = row["attributes"]
            if attrs["address"] != position.pool_address:
                raise ValueError("Quote pool identity mismatch")
            side = None
            for name in ("base", "quote"):
                token_id = row["relationships"][name + "_token"]["data"]["id"]
                if token_id == "solana_" + position.token:
                    side = name
            if side is None:
                raise ValueError("Quote token identity mismatch")
            price = price_value(attrs[side + "_token_price_usd"])
            return {"price": price, "source": "GeckoTerminal pool " + position.pool_address,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "provider_updated_at": None}
        except Exception as exc:
            errors.append("GeckoTerminal: " + str(exc))
        try:
            payload = await self.get_json(CG, params={"contract_addresses": position.token,
                "vs_currencies": "usd", "include_last_updated_at": "true"})
            row = payload[position.token]
            price = price_value(row["usd"])
            updated = datetime.fromtimestamp(price_value(row["last_updated_at"]), timezone.utc)
            observed = datetime.now(timezone.utc)
            age = (observed - updated).total_seconds()
            if not 0 <= age <= config.max_quote_age_seconds:
                raise ValueError("CoinGecko quote timestamp stale or in future")
            return {"price": price, "source": "CoinGecko listed Solana contract",
                    "observed_at": observed.isoformat(), "provider_updated_at": updated.isoformat()}
        except Exception as exc:
            errors.append("CoinGecko: " + str(exc))
        raise ValueError("; ".join(errors))


def position_views(ledger, prices, observations=None, at=None):
    at = at or datetime.now(timezone.utc)
    observations = observations or {}
    views = []
    for p in ledger.positions.values():
        price = prices.get(p.token)
        if price is not None:
            price = price_value(price)
        stale = price is None
        pnl = None if stale else ((price * (1 - ledger.slippage) * (1 - ledger.fee)
                                   - p.entry_price) * p.size - p.fees_paid)
        details = observations.get(p.id, {})
        views.append(dict(p.model_dump(mode="json"), current_price=price,
                          unrealized_pnl=pnl, stale_quoted=stale,
                          age_hours=max(0, (at - p.opened_at).total_seconds() / 3600),
                          stop_distance_pct=None if stale else (price - p.stop_loss) / price * 100,
                          target_distance_pct=None if stale or p.take_profit is None else
                          (p.take_profit - price) / price * 100,
                          quote=details))
    return views


def process_positions(ledger, settings, prices, observations=None, *, config=None, at=None):
    """Append marks before exits. Missing quotes never trigger even age exits."""
    config = config or PositionSettings()
    at = at or datetime.now(timezone.utc)
    prices = {token: price_value(value) for token, value in prices.items()}
    views = position_views(ledger, prices, observations, at)
    ledger.record_position_quotes(views, at)
    missing = [p["token"] for p in views if p["stale_quoted"]]
    if not missing:
        ledger.account_state(prices)
    executor = PaperExecutor(ledger, settings)
    exits = []
    for p in list(ledger.positions.values()):
        if p.token not in prices:
            LOG.warning("POSITION_STALE id=%s token=%s quote unavailable; no mark or exit", p.id, p.token)
            continue
        price = prices[p.token]
        reason = None
        if price <= p.stop_loss:
            reason = "stop_loss"
        elif p.take_profit is not None and price >= p.take_profit:
            reason = "take_profit"
        elif (at - p.opened_at).total_seconds() >= config.max_holding_hours * 3600:
            reason = "max_age"
        if reason:
            trade = executor.close(p.id, price, reason,
                RiskCheckResult(accepted=True, reason="Risk-reducing paper position exit"))
            exits.append(trade.model_dump(mode="json"))
            LOG.info("PAPER_EXIT id=%s reason=%s observed_quote=%s net_pnl=%s", p.id,
                     reason, price, trade.realized_pnl)
    LOG.info("POSITION_MANAGEMENT monitored=%s stale=%s exits=%s remaining=%s",
             len(views), len(missing), len(exits), len(ledger.positions))
    return {"observations": views, "exits": exits, "stale_tokens": missing,
            "prices": prices, "open_before": len(views), "open_after": len(ledger.positions)}


async def manage_positions(ledger, settings, *, client=None, config=None):
    config = config or PositionSettings()
    prices, observations = {}, {}
    # No request is made for an empty portfolio.
    if ledger.positions:
        async with PositionQuoteCollector(client=client, timeout=settings.http_timeout,
                                          retries=settings.http_retries) as collector:
            for p in ledger.positions.values():
                try:
                    observation = await collector.fetch(p, config)
                    prices[p.token] = observation["price"]
                    observations[p.id] = observation
                except Exception as exc:
                    observations[p.id] = {"price": None, "error": str(exc), "stale_quoted": True}
                    LOG.warning("POSITION_QUOTE_FAILED id=%s %s", p.id, exc)
    return process_positions(ledger, settings, prices, observations, config=config)
