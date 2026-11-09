"""Validated, immutable research and simulation records. Money is USD."""
from typing import Annotated
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarketSnapshot(Record):
    symbol: str
    price_usd: Positive
    market_cap_usd: Nonnegative
    volume_24h_usd: Nonnegative
    change_24h_pct: Finite
    fetched_at: AwareDatetime
    source: str


class TokenDiscovery(Record):
    name: str | None = None
    symbol: str | None = None
    mint_address: str
    pool_address: str
    first_seen_at: AwareDatetime
    price_usd: Positive | None = None
    liquidity_usd: Nonnegative | None = None
    volume_24h_usd: Nonnegative | None = None
    buys_24h: int | None = Field(default=None, ge=0)
    sells_24h: int | None = Field(default=None, ge=0)
    source_url: str
    unverified_fields: list[str] = Field(default_factory=list)


class SecurityAssessment(Record):
    mint_authority_revoked: bool | None = None
    freeze_authority_revoked: bool | None = None
    lp_locked_pct: Percent | None = None
    top_holder_concentration_pct: Percent | None = None
    risks: list[str] = Field(default_factory=list)
    score: Nonnegative | None = None
    source: str


class TradeProposal(Record):
    token: str = Field(min_length=1)
    pool_address: str | None = None
    entry_price: Positive
    size: Positive
    stop_loss: Positive | None = None
    take_profit: Positive | None = None
    leverage: Positive = 1
    strategy: str = "manual-paper"
    reason: str = Field(min_length=1)


class AccountState(Record):
    equity: Positive
    cash: Nonnegative
    day_start_equity: Positive
    peak_equity: Positive
    daily_realized_pnl: Finite = 0
    open_risk_usd: Nonnegative = 0


class RiskCheckResult(Record):
    accepted: bool
    reason: str
    risk_usd: Nonnegative = 0


class PaperPosition(Record):
    id: str = Field(default_factory=lambda: str(uuid4()))
    token: str
    pool_address: str | None = None
    entry_price: Positive
    size: Positive
    stop_loss: Positive
    take_profit: Positive | None = None
    fees_paid: Nonnegative
    opened_at: AwareDatetime
    strategy: str
    reason: str

    @model_validator(mode="after")
    def valid_levels(self):
        if self.stop_loss >= self.entry_price:
            raise ValueError("Long position stop must be below entry")
        if self.take_profit is not None and self.take_profit <= self.entry_price:
            raise ValueError("Take profit must exceed entry")
        return self


class PaperTrade(PaperPosition):
    exit_price: Positive
    exit_price_basis: Positive | None = None
    closed_at: AwareDatetime
    exit_reason: str
    realized_pnl: Finite
