"""Evidence-based, fail-closed PAPER strategy. No wallet or exchange access."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from act.domain.models import Record, SecurityAssessment, TokenDiscovery, TradeProposal
from act.collectors.solana_rpc import SecuritySettings
from act.paper.executor import PaperExecutor
from act.risk.limits import check_trade
from act.volume_profile import price_position, profile_stop, VolumeProfileFetcher


class StrategySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STRATEGY_", extra="ignore", frozen=True, allow_inf_nan=False)
    max_rugcheck_score: float = Field(default=500, gt=0, allow_inf_nan=False)
    min_lp_locked_pct: float = Field(default=80, ge=0, le=100)
    min_liquidity_usd: float = Field(default=10000, gt=0)
    min_volume_24h_usd: float = Field(default=25000, gt=0)
    min_buys_sells_ratio: float = Field(default=1.1, gt=0)
    max_pool_age_hours: float = Field(default=72, gt=0)
    stop_fraction: float = Field(default=0.15, gt=0, lt=1)
    target_fraction: float = Field(default=0.40, gt=0)
    max_open_positions: int = Field(default=3, ge=1, le=3)


class DecisionRecord(Record):
    id: str = Field(default_factory=lambda: str(uuid4()))
    cycle_id: str
    mint_address: str
    symbol: str | None
    risk_score: float | None
    decision: Literal["BUY", "REJECT", "WATCH"]
    reasoning: str
    proposal: dict | None = None
    created_at: AwareDatetime
    sources: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


def evaluate(pool: TokenDiscovery, security: SecurityAssessment | None, *,
             cycle_id: str, account, fee: float = 0.0025, slippage: float = 0.005,
             config: StrategySettings | None = None, at=None, volume_profile=None, screening_only=False) -> DecisionRecord:
    cfg = config or StrategySettings()
    at = at or datetime.now(timezone.utc)
    missing = []
    failures = []
    evidence = [f"ASSET {pool.symbol or 'unknown symbol'} ({pool.mint_address})"]
    for field in ("score", "lp_locked_pct", "mint_authority_revoked",
                  "freeze_authority_revoked", "top_holder_concentration_pct"):
        value = getattr(security, field, None)
        evidence.append(f"{field}={value}")
        if value is None:
            missing.append(field)
    score = security.score if security else None
    if score is not None and score >= cfg.max_rugcheck_score:
        failures.append(f"RugCheck score {score} must be below {cfg.max_rugcheck_score}; higher is worse")
    if security:
        evidence.extend(security.risks)
        failures.extend(r for r in security.risks if r.startswith("Security disagreement:"))
        security_cfg = SecuritySettings()
        if (security_cfg.security_onchain_enabled and security.top_holder_concentration_pct is not None
                and security.top_holder_concentration_pct >= security_cfg.security_max_top_holders_pct * 100):
            failures.append(f"Top-holder concentration must be below {security_cfg.security_max_top_holders_pct * 100}%")
        if security.lp_locked_pct is not None and security.lp_locked_pct < cfg.min_lp_locked_pct:
            failures.append(f"LP locked below {cfg.min_lp_locked_pct}%")
        for field in ("mint_authority_revoked", "freeze_authority_revoked"):
            if getattr(security, field) is False:
                failures.append(f"{field}=False: active authority")
    for field, minimum in (("liquidity_usd", cfg.min_liquidity_usd),
                           ("volume_24h_usd", cfg.min_volume_24h_usd)):
        value = getattr(pool, field)
        evidence.append(f"{field}={value}; minimum={minimum}")
        if value is None:
            missing.append(field)
        elif value < minimum:
            failures.append(f"{field} below {minimum}")
    evidence.append(f"buys={pool.buys_24h}; sells={pool.sells_24h}; required ratio={cfg.min_buys_sells_ratio}")
    if pool.buys_24h is None or pool.sells_24h is None:
        missing.append("buys_24h/sells_24h")
    elif pool.sells_24h == 0:
        # A zero denominator is not reliable buy/sell evidence.
        failures.append("Undefined buys/sells ratio: sells=0")
    elif pool.buys_24h / pool.sells_24h < cfg.min_buys_sells_ratio:
        failures.append(f"buys/sells ratio {pool.buys_24h / pool.sells_24h:.4f} below minimum")
    age = (at - pool.first_seen_at).total_seconds() / 3600
    evidence.append(f"pool age hours={age:.4f}; maximum={cfg.max_pool_age_hours}")
    if not 0 <= age <= cfg.max_pool_age_hours:
        failures.append("Pool age outside allowed range (or future timestamp)")
    if pool.price_usd is None:
        missing.append("price_usd")
    if pool.unverified_fields:
        missing.extend(pool.unverified_fields)
    if missing:
        failures.append("Missing/unverified data: " + ", ".join(sorted(set(missing))))
    if account is None:
        failures.append("Current equity unavailable; no position sizing permitted")
    proposal = None
    if screening_only:
        return DecisionRecord(cycle_id=cycle_id, mint_address=pool.mint_address,
            symbol=pool.symbol, risk_score=score, sources=getattr(pool, "sources", ["geckoterminal_new"]), failure_reasons=list(failures), missing_fields=sorted(set(missing)), decision="REJECT" if failures else "WATCH",
            reasoning="; ".join(evidence + failures + ["Hard-filter preflight only; no trade"]),
            proposal=None, created_at=at)
    vp = volume_profile or {"status": "unavailable", "reason": "OHLCV profile not supplied"}
    if not failures:
        proposal = {"volume_profile": vp}
        if vp.get("status") != "available":
            failures.append("volume_profile: unavailable: " + vp.get("reason", "unknown failure"))
        else:
            vp = dict(vp, price_vs_value_area=price_position(pool.price_usd, vp))
            proposal = {"volume_profile": vp}
            evidence.append("volume_profile=" + str(vp))
            if vp["price_vs_value_area"] == "below_VAL":
                failures.append("Price below VAL: volume-profile invalidation")
            elif vp["price_vs_value_area"] != "at_or_above_VAH":
                failures.append("Price inside value area: acceptance requires price >= VAH")
            elif vp["val"] >= pool.price_usd:
                failures.append("Degenerate profile: VAL must be strictly below entry")
    else:
        evidence.append("volume_profile: not_attempted (hard filters/equity failed)")
    if not failures:
        entry = pool.price_usd
        stop, stop_basis = profile_stop(entry, vp["val"], cfg.stop_fraction)
        evidence.append(stop_basis)
        target = entry * (1 + cfg.target_fraction)
        risk_budget = account.equity * 0.01
        nominal_notional = risk_budget / ((entry - stop) / entry)
        entry_cost = entry * (1 + slippage) * (1 + fee)
        stop_proceeds = stop * (1 - slippage) * (1 - fee)
        size = min(nominal_notional / entry, risk_budget / (entry_cost - stop_proceeds)) * (1 - 1e-10)
        trade = TradeProposal(token=pool.mint_address, pool_address=pool.pool_address,
                              entry_price=entry, size=size,
                              stop_loss=stop, take_profit=target, strategy="screened-paper-v1",
                              reason="; ".join(evidence))
        gate = check_trade(trade, account, fee_pct=fee * 100, slippage_pct=slippage * 100)
        if not gate.accepted:
            failures.append(gate.reason)
        else:
            fees = size * fee * (entry * (1 + slippage) + stop * (1 - slippage))
            slip = size * slippage * (entry + stop)
            reward = size * (target * (1 - slippage) * (1 - fee) - entry_cost)
            proposal = dict(trade.model_dump(mode="json"), volume_profile=vp, stop_basis=stop_basis,
                            entry_price_basis="GeckoTerminal base_token_price_usd (USD quote)",
                            nominal_position_notional_usd=nominal_notional,
                            position_notional_usd=size * entry, risk_budget_usd=risk_budget,
                            max_estimated_loss=gate.risk_usd, estimated_fees=fees,
                            estimated_slippage=slip, risk_reward_ratio=reward / gate.risk_usd,
                            data_timestamp=at.isoformat(), pool_created_at=pool.first_seen_at.isoformat())
            evidence.append("All evidence filters and inclusive-cost risk checks passed; SIMULATED PAPER ONLY")
    return DecisionRecord(cycle_id=cycle_id, mint_address=pool.mint_address,
                          symbol=pool.symbol, risk_score=score, sources=getattr(pool, "sources", ["geckoterminal_new"]), failure_reasons=list(failures), missing_fields=sorted(set(missing)),
                          decision="REJECT" if failures else "BUY",
                          reasoning="; ".join(evidence + failures), proposal=proposal, created_at=at)


def run_strategy(pools, assessments, ledger, settings, cycle_id, *, config=None, at=None,
                 position_prices=None, volume_profiles=None):
    cfg = config or StrategySettings()
    at = at or datetime.now(timezone.utc)
    executor = PaperExecutor(ledger, settings)
    prices = {p.mint_address: p.price_usd for p in pools if p.price_usd is not None}
    if position_prices is not None:
        # Dedicated position refresh is authoritative, including a failed refresh.
        # A discovery row must not silently erase the stale flag for an old holding.
        for position in ledger.positions.values():
            prices.pop(position.token, None)
        prices.update(position_prices)
    missing_marks = sorted({p.token for p in ledger.positions.values()} - prices.keys())
    decisions = []
    for pool in pools:
        security = assessments.get(pool.mint_address)
        if isinstance(security, dict):
            security = SecurityAssessment.model_validate(security)
        if missing_marks:
            evidence = evaluate(pool, security, cycle_id=cycle_id, account=None,
                                fee=ledger.fee, slippage=ledger.slippage, config=cfg, at=at)
            decision = evidence.model_copy(update={"decision": "WATCH", "failure_reasons": evidence.failure_reasons + ["Missing current marks for open positions"],
                "reasoning": evidence.reasoning + "; Missing current marks for open positions: "
                + ", ".join(missing_marks)})
        else:
            decision = evaluate(pool, security, cycle_id=cycle_id,
                                account=ledger.account_state(prices), fee=ledger.fee,
                                slippage=ledger.slippage, config=cfg, at=at,
                                volume_profile=(volume_profiles or {}).get(pool.pool_address))
            if decision.decision == "BUY":
                reason = None
                if len(ledger.positions) >= cfg.max_open_positions:
                    reason = f"Concurrent open position cap {cfg.max_open_positions} reached"
                elif any(p.token == pool.mint_address for p in ledger.positions.values()):
                    reason = "Already holding this mint; duplicate exposure rejected"
                if reason:
                    decision = decision.model_copy(update={"decision": "REJECT", "reasoning": decision.reasoning + "; " + reason, "failure_reasons": decision.failure_reasons + [reason]})
                else:
                    fields = TradeProposal.model_fields
                    trade = TradeProposal(**{k: v for k, v in decision.proposal.items() if k in fields})
                    try:
                        position = executor.open(trade, prices)
                    except ValueError as exc:
                        decision = decision.model_copy(update={"decision": "REJECT", "failure_reasons": decision.failure_reasons + ["executor gate: " + str(exc)], "reasoning": decision.reasoning + "; executor gate: " + str(exc)})
                    else:
                        decision = decision.model_copy(update={"reasoning": decision.reasoning + "; paper position opened " + position.id})
        decisions.append(decision.model_dump(mode="json"))
    return decisions, prices


async def collect_volume_profiles(pools, assessments, ledger, settings, *,
                                  position_prices=None, client=None, at=None):
    """No OHLCV HTTP until ALL hard filters pass. No executor in preflight."""
    at = at or datetime.now(timezone.utc)
    prices = {p.mint_address: p.price_usd for p in pools if p.price_usd is not None}
    for position in ledger.positions.values():
        prices.pop(position.token, None)
    prices.update(position_prices or {})
    missing = {p.token for p in ledger.positions.values()} - prices.keys()
    account = None if missing else ledger.account_state(prices)
    profiles = {}
    async with VolumeProfileFetcher(client=client, timeout=settings.http_timeout,
                                    retries=settings.http_retries) as fetcher:
        for pool in pools:
            security = assessments.get(pool.mint_address)
            if isinstance(security, dict):
                security = SecurityAssessment.model_validate(security)
            screening = evaluate(pool, security, cycle_id="profile-preflight", account=account,
                                 at=at, screening_only=True)
            if screening.decision != "REJECT":
                profiles[pool.pool_address] = await fetcher.profile(pool, at=at)
    return profiles
