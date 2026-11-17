"""Binance PAPER ONLY agents. Public reads; no exchange order/account interface.


Append-only events share PaperLedger and its existing Supabase snapshot/RPC.
Solana execution and account state are deliberately not inputs to these agents.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
from html import escape
import json
import logging
import os
from statistics import correlation


import httpx


from act.agents import Context, budget
from act.agents.scanner import TIMEFRAMES, candles, directional_score, indicators, structure
from act.binance_data import BinanceData, BinanceError, number


LOG = logging.getLogger(__name__)
SPOT_MAKER_RATE = .001
SPOT_TAKER_RATE = .001
FUTURES_MAKER_RATE = .0002
FUTURES_TAKER_RATE = .0005
MODEL = "post-decision-1m-close + half-spread + top20-VWAP-impact-v1"
MARKETS = ("spot", "futures")




def stamp():
    return datetime.now(timezone.utc).isoformat()




def ms(value):
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("Timezone required")
    return at.timestamp() * 1000




def positive(value):
    value = number(value)
    if value <= 0:
        raise ValueError("Positive finite value required")
    return value




def fresh(source, now, seconds=120):
    age = (ms(now) - ms(source["fetched_at"])) / 1000
    if not -60 <= age <= seconds:
        raise ValueError("STALE_OR_FUTURE public data; no fill")




def fee_rate(market, maker=False):
    if market not in MARKETS:
        raise ValueError("Unknown Binance market")
    default = {( "spot", True): SPOT_MAKER_RATE, ("spot", False): SPOT_TAKER_RATE,
               ("futures", True): FUTURES_MAKER_RATE, ("futures", False): FUTURES_TAKER_RATE}
    key = "BINANCE_" + market.upper() + ("_MAKER_RATE" if maker else "_TAKER_RATE")
    value = number(os.getenv(key, default[market, maker]))
    if not 0 <= value <= .01:
        raise ValueError("Invalid fee rate: " + key)
    return value




@dataclass(frozen=True)
class RiskLimits:
    risk_fraction: float = .005
    max_positions: int = 4
    max_exposure: float = .5
    instrument_exposure: float = .15
    daily_loss: float = .02
    correlation_limit: float = .8
    latency_seconds: int = 5
    max_pending_seconds: int = 1800


    def __post_init__(self):
        for key in ("risk_fraction", "max_exposure", "instrument_exposure", "daily_loss", "correlation_limit"):
            if not 0 < number(getattr(self, key)) <= 1:
                raise ValueError("Invalid risk limit " + key)
        if not 1 <= self.max_positions <= 20 or not 1 <= self.latency_seconds <= 60:
            raise ValueError("Invalid count/latency")




def initial_state():
    return {"version": 1, "positions": {}, "pending": [], "trades": [], "used": [],
            "halt": None, "daily_halt": None, "day": None, "day_start": 20000.,
            "accounts": {m: {"initial": 10000., "cash": 10000.} for m in MARKETS}}




def restore(ledger):
    events = [e for e in ledger.history if e["kind"] == "binance_paper"]
    return deepcopy(events[-1]["state"]) if events else initial_state()




def round_size(size, meta):
    filters = {r["filterType"]: r for r in meta["filters"]}
    lot = filters["LOT_SIZE"]
    step = Decimal(str(positive(lot["stepSize"])))
    return float((Decimal(str(positive(size))) / step).to_integral_value(rounding=ROUND_DOWN) * step)




def minimums(size, price, meta):
    q, p = Decimal(str(positive(size))), Decimal(str(positive(price)))
    filters = {r["filterType"]: r for r in meta["filters"]}
    if meta.get("status") != "TRADING" or meta.get("quoteAsset") != "USDT":
        raise ValueError("Only TRADING USDT instruments supported; no conversion invented")
    for name in ("LOT_SIZE", "MARKET_LOT_SIZE"):
        if name not in filters:
            if name == "LOT_SIZE":
                raise ValueError("Missing LOT_SIZE")
            continue
        f = filters[name]
        low, high, step = (Decimal(str(f[k])) for k in ("minQty", "maxQty", "stepSize"))
        if q < low or (high > 0 and q > high) or (step > 0 and q % step != 0):
            raise ValueError(name + " rejected simulated quantity")
    f = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
    if not f:
        raise ValueError("Missing MIN_NOTIONAL/NOTIONAL")
    low = Decimal(str(f.get("minNotional", f.get("notional"))))
    if q * p < low or (f.get("maxNotional") and q * p > Decimal(str(f["maxNotional"]))):
        raise ValueError("MIN_NOTIONAL/NOTIONAL rejected simulated quantity")




def fill_price(book, bars, quantity, decision_at, now, latency=5, sell=False):
    """Use only completed, fresh bars strictly AFTER decision + delay; never same-price fills."""
    fresh(book.provenance, now, 15)
    fresh(bars.provenance, now)
    rows = [r for r in bars.data if number(r[0]) >= ms(decision_at) + latency * 1000
            and number(r[6]) < ms(now)]
    if not rows:
        raise ValueError("WAIT_LATENCY: no completed post-decision 1m candle")
    row = max(rows, key=lambda r: number(r[0]))
    if not 0 <= ms(now) - number(row[6]) <= 120000:
        raise ValueError("STALE_KLINE: no fabricated fills")
    anchor = positive(row[4])
    bids = sorted([(positive(p), positive(q)) for p, q in book.data["bids"]], reverse=True)
    asks = sorted([(positive(p), positive(q)) for p, q in book.data["asks"]])
    if not bids or not asks or asks[0][0] <= bids[0][0]:
        raise ValueError("Empty/crossed depth")
    mid = (asks[0][0] + bids[0][0]) / 2
    top = bids[0][0] if sell else asks[0][0]
    remaining, cost = positive(quantity), 0.
    for p, q in bids if sell else asks:
        take = min(remaining, q)
        cost += take * p
        remaining -= take
        if remaining <= quantity * 1e-12:
            break
    if remaining > quantity * 1e-12:
        raise ValueError("INSUFFICIENT_DEPTH: no extrapolated fill")
    impact = abs(cost / quantity - top)
    spread = (asks[0][0] - bids[0][0]) / 2
    # Large mismatches between the delayed bar and current book veto instead of inventing liquidity.
    if abs(anchor / mid - 1) > .02:
        raise ValueError("KLINE_DEPTH_DIVERGENCE >2%; no fill")
    price = positive(anchor + (-1 if sell else 1) * (spread + impact))
    return price, {"model": MODEL, "bar_open_ms": row[0], "bar_close_ms": row[6],
                   "anchor": anchor, "mid": mid, "half_spread": spread, "impact": impact,
                   "decision_at": decision_at, "observed_at": now,
                   "depth_source": book.provenance, "kline_source": bars.provenance}




def funding(position, rows, now):
    """Long-only, unlevered perps. Actual timestamp/rate/mark, not an assumed 8h clock."""
    cutoff = position.get("funding_through", position["opened_at"])
    ordered = sorted(rows, key=lambda r: number(r["fundingTime"]))
    if not ordered or number(ordered[0]["fundingTime"]) > ms(cutoff):
        raise ValueError("FUNDING_HISTORY_GAP; halt rather than assume zero")
    amount, evidence = 0., []
    for row in ordered:
        if row["symbol"] != position["symbol"]:
            raise ValueError("Funding symbol mismatch")
        if ms(cutoff) < number(row["fundingTime"]) <= ms(now):
            cost = position["size"] * positive(row["markPrice"]) * number(row["fundingRate"])
            amount += cost
            evidence.append({**row, "cost": cost, "size": position["size"]})
    return amount, evidence




def portfolio(state):
    out = {}
    for market in MARKETS:
        positions = [p for p in state["positions"].values() if p["market"] == market]
        valid = all(p.get("mark") is not None for p in positions)
        cash = state["accounts"][market]["cash"]
        value = sum(p["size"] * p["mark"] for p in positions) if valid else None
        out[market] = {"cash": cash, "equity": cash + value if valid else None,
                       "exposure": value, "open_positions": len(positions),
                       "unrealized_pnl": sum((p["mark"] - p["entry_price"]) * p["size"]
                           - p["fees_paid"] - p["funding_paid"] for p in positions) if valid else None}
    out["total"] = {k: sum(out[m][k] for m in MARKETS)
                    if all(out[m][k] is not None for m in MARKETS) else None
                    for k in ("cash", "equity", "exposure", "open_positions", "unrealized_pnl")}
    return out




class RiskManagementAgent:
    name = "risk_management"


    def __init__(self, limits=None):
        self.limits = limits or RiskLimits()


    def describe(self):
        return "PAPER ONLY; VETO authority; no recovery sizing or activity targets"


    def approve(self, state, candidate, meta, now, *, filling=False):
        lim = self.limits
        if state["halt"]:
            raise ValueError("EMERGENCY_HALT: " + state["halt"])
        if state["daily_halt"] == now[:10]:
            raise ValueError("DAILY_LOSS_HALT until next UTC day")
        total = portfolio(state)["total"]
        equity = total["equity"]
        if equity is None or equity <= 0:
            raise ValueError("STALE_OR_MISSING_MARKS")
        if equity <= state["day_start"] * (1 - lim.daily_loss):
            state["daily_halt"] = now[:10]
            raise ValueError("DAILY_LOSS_HALT until next UTC day")
        if len(state["positions"]) + (0 if filling else len(state["pending"])) >= lim.max_positions:
            raise ValueError("MAX_SIMULTANEOUS_POSITIONS")
        if candidate["id"] in state["used"]:
            raise ValueError("DUPLICATE_ORDER")
        market = candidate["market"]
        if market not in MARKETS or candidate.get("quote_asset", "USDT") != "USDT":
            raise ValueError("UNSUPPORTED_MARKET_OR_QUOTE")
        fresh({"fetched_at": candidate["as_of"]}, now, 1800 if filling else 120)
        if set(candidate["frames"]) != set(TIMEFRAMES) or candidate["up_score"] < .6:
            raise ValueError("TA_INCOMPLETE_OR_NOT_CORROBORATED")
        if not candidate.get("timeframe_agreement"):
            raise ValueError("TIMEFRAME_DISAGREEMENT")
        f = candidate["frames"]["1m"]
        entry = positive(candidate.get("fill_price", f["last_close"]))
        atr = positive(f["atr14"])
        support = positive(f["market_structure"]["support"])
        stop = min(entry - 2 * atr, support - .25 * atr)
        target = max(entry + 3 * atr, positive(f["market_structure"]["resistance"]))
        if not 0 < stop < entry < target:
            raise ValueError("INVALID_TA_LEVELS")
        commitments = list(state["positions"].values()) + ([] if filling else state["pending"])
        for p in commitments:
            if p["underlying"] == meta["baseAsset"]:
                raise ValueError("SAME_UNDERLYING_SPOT_FUTURES")
            a, b = candidate.get("returns", []), p.get("returns", [])
            # Unknown correlation never means independent; match by candle timestamps.
            pa = dict(a)
            pb = dict(b)
            times = sorted(pa.keys() & pb.keys())
            if len(times) < 20:
                raise ValueError("CORRELATION_UNVERIFIED n=" + str(len(times)))
            try:
                rho = correlation([pa[k] for k in times], [pb[k] for k in times])
            except (ValueError, ZeroDivisionError):
                raise ValueError("CORRELATION_UNVERIFIED constant series") from None
            if abs(rho) >= lim.correlation_limit:
                raise ValueError(f"HIGH_CORRELATION rho={rho} n={len(times)}")
        rate = fee_rate(market)
        # Reserve round-trip fees and a conservative 2% adverse fill allowance in stop risk.
        size = min(equity * lim.risk_fraction / (entry - stop + entry * (2 * rate + .02)),
                   equity * lim.instrument_exposure / entry,
                   state["accounts"][market]["cash"] / (entry * (1 + rate)))
        size = round_size(size, meta)
        minimums(size, entry, meta)
        reserve = sum(p.get("notional", p.get("entry_price", 0) * p["size"]) for p in commitments)
        if reserve + size * entry > equity * lim.max_exposure:
            raise ValueError("MAX_TOTAL_EXPOSURE")
        if size * entry > equity * lim.instrument_exposure + 1e-8:
            raise ValueError("PER_INSTRUMENT_EXPOSURE")
        return {**candidate, "size": size, "notional": size * entry, "underlying": meta["baseAsset"],
                "stop_loss": stop, "take_profit": target, "atr": atr,
                "approval_reason": "Scanner + five-frame TA agreement; all risk caps passed",
                "risk_budget": equity * lim.risk_fraction}




class TradeExecutionSimulationAgent:
    name = "trade_execution_simulation"


    def describe(self):
        return "PAPER ONLY public-data delayed fills; no live counterpart"


    def open(self, state, approved, price, evidence, now):
        market = approved["market"]
        quantity = approved["size"]
        fee = price * quantity * fee_rate(market)
        cost = price * quantity + fee
        if cost > state["accounts"][market]["cash"]:
            raise ValueError("INSUFFICIENT_PAPER_CASH")
        if approved["id"] in state["used"]:
            raise ValueError("DUPLICATE_ORDER")
        p = {**approved, "token": approved["symbol"], "entry_price": price,
             "opened_at": now, "fees_paid": fee, "funding_paid": 0., "funding_through": now,
             "mark": price, "mark_at": now, "peak": price, "partial": False,
             "execution": evidence, "exit_intent": None}
        state["positions"][p["id"]] = p
        state["used"].append(p["id"])
        state["accounts"][market]["cash"] -= cost
        return p


    def close(self, state, p, quantity, price, evidence, now, reason):
        ratio = quantity / p["size"]
        fee = price * quantity * fee_rate(p["market"])
        entry_fee, funding_cost = p["fees_paid"] * ratio, p["funding_paid"] * ratio
        trade = {**deepcopy(p), "id": p["id"] + ":" + now, "position_id": p["id"],
                 "size": quantity, "exit_price": price, "exit_price_basis": evidence["anchor"],
                 "closed_at": now, "exit_reason": reason, "fees_paid": entry_fee + fee,
                 "funding_paid": funding_cost, "execution_exit": evidence,
                 "realized_pnl": (price - p["entry_price"]) * quantity - entry_fee - fee - funding_cost}
        state["accounts"][p["market"]]["cash"] += price * quantity - fee
        state["trades"].append(trade)
        if ratio >= 1 - 1e-12:
            del state["positions"][p["id"]]
        else:
            p["size"] -= quantity
            p["fees_paid"] -= entry_fee
            p["funding_paid"] -= funding_cost
            p["partial"] = True
        return trade




def returns(bars, now):
    rows = candles(bars.data, ms(now) / 1000)
    if ms(now) - rows[-1][0] > 180000:
        raise ValueError("STALE_1M_EVIDENCE")
    return [[int(b[0]), b[4] / a[4] - 1] for a, b in zip(rows, rows[1:])]




async def run_pipeline(report, ledger, *, client=None, limits=None):
    """Single writer; record every approval/veto/fill in the existing event ledger.


    Pending decisions survive cycles; open risk is managed before new approvals.
    Any critical error persists a sticky emergency halt. Exits remain permitted.
    """
    if report.get("paper_only") is not True:
        raise ValueError("PAPER ONLY invariant required")
    state = restore(ledger)
    risk, executor = RiskManagementAgent(limits), TradeExecutionSimulationAgent()
    now = stamp()
    for position in state["positions"].values():
        position["mark"], position["mark_at"] = None, None
    decisions, evidence = [], []
    contexts, apis, infos, cache = {}, {}, {}, {}
    def record(action, reason, **details):
        row = {"at": now, "action": action, "reason": reason, **details}
        decisions.append(row)
        LOG.warning("BINANCE_PAPER_DECISION %s", json.dumps(row, allow_nan=False))
    async with httpx.AsyncClient() as owned:
        for m in MARKETS:
            contexts[m] = Context({}, client if client is not None else owned,
                                  budget(executor.name, "TIME_BUDGET_SECONDS", 30),
                                  int(budget(executor.name, "FETCH_BUDGET", 20)))
            apis[m] = BinanceData(contexts[m], m)
        async def data(m, symbol):
            key = (m, symbol)
            if key not in cache:
                bars = await apis[m].klines(symbol, "1m")
                fresh(bars.provenance, now)
                rs = returns(bars, now)
                cache[key] = (bars, rs)
            return cache[key]
        async def meta(m, symbol):
            if m not in infos:
                response = await apis[m].exchange_info()
                fresh(response.provenance, now)
                infos[m] = {r["symbol"]: r for r in response.data["symbols"]}
            if symbol not in infos[m]:
                raise ValueError("Instrument missing from exchangeInfo")
            return infos[m][symbol]
        try:
            # Mark only from newly fetched candles, including when entries are halted.
            for p in list(state["positions"].values()):
                p["mark"], p["mark_at"] = None, None
                try:
                    bars, rs = await data(p["market"], p["symbol"])
                    complete = [r for r in bars.data if number(r[6]) < ms(now)]
                    p.update(mark=positive(complete[-1][4]), mark_at=bars.provenance["fetched_at"], returns=rs)
                    if p["market"] == "futures":
                        rates = await apis["futures"].funding_rate(p["symbol"])
                        cost, events = funding(p, rates.data, now)
                        state["accounts"]["futures"]["cash"] -= cost
                        p["funding_paid"] += cost
                        p["funding_through"] = now
                        evidence.extend(events)
                    if p["exit_intent"]:
                        intent = p["exit_intent"]
                        book = await apis[p["market"]].depth(p["symbol"])
                        metadata = await meta(p["market"], p["symbol"])
                        q = round_size(p["size"] / 2, metadata) if intent["reason"] == "partial_profit" else p["size"]
                        # Do not create dust: partials fall back to a full exit if either leg is invalid.
                        if intent["reason"] == "partial_profit":
                            try:
                                minimums(q, p["mark"], metadata)
                                minimums(p["size"] - q, p["mark"], metadata)
                            except ValueError:
                                q = p["size"]
                        price, model = fill_price(book, bars, q, intent["at"], now,
                                                  risk.limits.latency_seconds, sell=True)
                        minimums(q, price, metadata)
                        trade = executor.close(state, p, q, price, model, now, intent["reason"])
                        record("CLOSE", intent["reason"], trade=trade)
                        p["exit_intent"] = None
                    if p["id"] not in state["positions"]:
                        continue
                    p["peak"] = max(p["peak"], p["mark"])
                    p["stop_loss"] = max(p["stop_loss"], p["peak"] - 2 * p["atr"])
                    reason = None
                    if p["mark"] <= p["stop_loss"]:
                        reason = "trailing_or_stop_loss"
                    elif p["mark"] >= p["take_profit"]:
                        reason = "take_profit"
                    elif not p["partial"] and p["mark"] >= p["entry_price"] + 1.5 * p["atr"]:
                        reason = "partial_profit"
                    ta = report.get("agents", {}).get("technical_" + p["market"], {}).get("data", {})
                    setup = next((c for c in ta.get("candidates", []) if c["symbol"] == p["symbol"]), None)
                    if setup and setup["up_score"] < .5:
                        reason = "entry_setup_invalidated_by_TA"
                    if p["mark"] < p["frames"]["1m"]["market_structure"]["support"]:
                        reason = "entry_support_broken"
                    if reason and not p["exit_intent"]:
                        p["exit_intent"] = {"at": now, "reason": reason}
                        record("EXIT_DECISION", reason, position_id=p["id"])
                except (ValueError, BinanceError) as exc:
                    record("NO_FILL", str(exc), position_id=p["id"])
                    if "WAIT_LATENCY" not in str(exc):
                        state["halt"] = str(exc)
                        p["mark"], p["mark_at"] = None, None
            total = portfolio(state)["total"]
            if state["day"] != now[:10] and total["equity"] is not None:
                state["day"], state["day_start"] = now[:10], total["equity"]
            if total["equity"] is not None and total["equity"] <= state["day_start"] * (1 - risk.limits.daily_loss):
                state["daily_halt"] = now[:10]
                record("HALT", "DAILY_LOSS_HALT")
            pending, state["pending"] = state["pending"], []
            for candidate in pending:
                try:
                    if ms(now) - ms(candidate["decision_at"]) > risk.limits.max_pending_seconds * 1000:
                        raise ValueError("EXPIRED_APPROVAL; re-analysis required")
                    bars, rs = await data(candidate["market"], candidate["symbol"])
                    book = await apis[candidate["market"]].depth(candidate["symbol"])
                    metadata = await meta(candidate["market"], candidate["symbol"])
                    price, model = fill_price(book, bars, candidate["size"], candidate["decision_at"],
                                              now, risk.limits.latency_seconds)
                    frames, provenance = {}, []
                    for interval in TIMEFRAMES:
                        current = bars if interval == "1m" else await apis[candidate["market"]].klines(candidate["symbol"], interval)
                        fresh(current.provenance, now)
                        completed = candles(current.data, ms(now) / 1000)
                        frames[interval] = dict(indicators(completed), market_structure=structure(completed))
                        provenance.append(current.provenance)
                    candidate.update(directional_score(list(frames.values())))
                    candidate.update(fill_price=price, returns=rs, frames=frames, as_of=now, provenance=provenance)
                    approved = risk.approve(state, candidate, metadata, now, filling=True)
                    # Never enlarge a previously approved order after price/equity changes.
                    approved["size"] = min(approved["size"], candidate["size"])
                    price, model = fill_price(book, bars, approved["size"], candidate["decision_at"],
                                              now, risk.limits.latency_seconds)
                    minimums(approved["size"], price, metadata)
                    p = executor.open(state, approved, price, model, now)
                    record("OPEN", approved["approval_reason"], position=deepcopy(p))
                except (ValueError, BinanceError) as exc:
                    if "WAIT_LATENCY" in str(exc):
                        state["pending"].append(candidate)
                    record("VETO", str(exc), candidate_id=candidate["id"])
            # New decisions are queued, never filled on the decision tick.
            for market in MARKETS:
                agents = report.get("agents") or {}
                scan = agents.get("scanner_" + market, {}).get("data", {})
                ta = agents.get("technical_" + market, {}).get("data", {})
                scanned = {r["symbol"]: r for r in scan.get("candidates", [])}
                analyzed = {r["symbol"]: r for r in ta.get("candidates", [])}
                for symbol in scanned.keys() - analyzed.keys():
                    record("VETO", "NO_COMPLETE_TA_CORROBORATION", market=market, symbol=symbol)
                for row in ta.get("candidates", []):
                    identity = market + ":" + row["symbol"] + ":" + str(row["frames"]["1m"]["market_structure"]["evidence"]["last_open_time_ms"])
                    candidate_id = hashlib.sha256(identity.encode()).hexdigest()
                    try:
                        source = scanned[row["symbol"]]
                        for provenance in list(source["provenance"]) + list(row["provenance"]):
                            fresh(provenance, now)
                        bars, rs = await data(market, row["symbol"])
                        candidate = {**source, **row, "id": candidate_id, "returns": rs,
                                     "as_of": now, "decision_at": now,
                                     "scanner_reasons": list(source["reasons"]),
                                     "disagreements": list(ta.get("disagreements", []))}
                        if any(p["id"] == candidate_id for p in state["pending"]):
                            raise ValueError("DUPLICATE_PENDING_ORDER")
                        approved = risk.approve(state, candidate, await meta(market, row["symbol"]), now)
                        state["pending"].append(approved)
                        record("APPROVE", approved["approval_reason"], candidate=approved)
                    except (ValueError, BinanceError, KeyError) as exc:
                        record("VETO", str(exc), candidate_id=candidate_id, market=market, symbol=row["symbol"])
        except Exception as exc:
            LOG.exception("BINANCE_PAPER_CRITICAL_HALT")
            state["halt"] = type(exc).__name__ + ": " + str(exc)
            record("HALT", state["halt"])
    for context in contexts.values():
        evidence.extend(context.provenance)
    event = {"kind": "binance_paper", "at": now, "cycle_id": report["cycle_id"],
             "state": state, "decisions": decisions, "provenance": evidence}
    if any(e.get("cycle_id") == report["cycle_id"] for e in ledger.history if e["kind"] == "binance_paper"):
        raise RuntimeError("Duplicate Binance cycle; no ledger append")
    ledger._append(event)
    accounts = portfolio(state)
    report["binance_paper"] = {"paper_only": True, "as_of": now, "accounts": accounts,
        "risk": {"limits": asdict(risk.limits), "emergency_halt": state["halt"],
                 "daily_halt": state["daily_halt"], "day_start_equity": state["day_start"],
                 "daily_loss_used": max(0, state["day_start"] - accounts["total"]["equity"])
                     if accounts["total"]["equity"] is not None else None,
                 "recent_vetoes": [d for d in decisions if d["action"] == "VETO"][-20:]},
        "decisions": decisions, "pending": state["pending"], "positions": list(state["positions"].values()),
        "closed_trades_n": len(state["trades"]), "funding_events": evidence}
    report["binance_learning"] = {"n": len(decisions), "records": decisions,
                                   "caveat": "Observed paper decisions only; no inferred profits"}
    views = []
    for p in state["positions"].values():
        views.append({**deepcopy(p), "token": p["symbol"], "current_price": p.get("mark"),
            "unrealized_pnl": ((p["mark"] - p["entry_price"]) * p["size"] - p["fees_paid"] - p["funding_paid"])
                if p.get("mark") is not None else None,
            "stop_loss": p["stop_loss"], "take_profit": p["take_profit"],
            "stale_quoted": p.get("mark") is None,
            "quote": {"fetched_at": p.get("mark_at"), "model": MODEL}})
    report.setdefault("paper_positions", []).extend(views)
    report.setdefault("paper_trades", []).extend(completed_positions(state))
    solana = deepcopy(report.get("paper_account") or {})
    report["market_accounts"] = {"solana": solana, **{m: accounts[m] for m in MARKETS}}
    if solana.get("equity") is not None and accounts["total"]["equity"] is not None:
        report["paper_account"] = {**solana, **{k: solana[k] + accounts["total"][k] for k in ("equity", "cash")}}
    else:
        report["paper_account"] = {"error": "Missing current market valuation; total unavailable"}
        report["valuation_status"] = "unavailable"
    if state["halt"] or state["daily_halt"] == now[:10]:
        report["status"] = "degraded"
    return state




def completed_positions(state):
    """One readiness observation per fully closed position, not per profit tranche."""
    groups = {}
    for trade in state["trades"]:
        groups.setdefault(trade["position_id"], []).append(trade)
    result = []
    for identity, rows in groups.items():
        if identity in state["positions"]:
            continue
        size = sum(r["size"] for r in rows)
        result.append({**deepcopy(rows[-1]), "id": identity, "token": rows[-1]["symbol"],
            "size": size, "tranches_n": len(rows),
            "exit_price": sum(r["exit_price"] * r["size"] for r in rows) / size,
            **{key: sum(r[key] for r in rows) for key in ("realized_pnl", "fees_paid", "funding_paid")}})
    return result


def paper_panel(report):
    data = report.get("binance_paper") or {}
    if not data:
        return '<section><h2>BINANCE PAPER risk</h2><p>No stored execution state.</p></section>'
    def safe(value):
        return escape(json.dumps(value, indent=2, allow_nan=False))
    out = '<section><h2>BINANCE PAPER positions / risk</h2><p>LAST STORED STATE: ' + escape(str(data.get("as_of"))) + '</p>'
    for p in data.get("positions", []):
        unrealized = ((p["mark"] - p["entry_price"]) * p["size"] - p["fees_paid"] - p["funding_paid"]
                      if p.get("mark") is not None else None)
        view = {k: p.get(k) for k in ("market", "symbol", "size", "entry_price", "mark", "mark_at", "take_profit", "stop_loss")}
        out += '<article><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + safe({**view, "unrealized_pnl": unrealized}) + '</pre></article>'
    out += '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + safe({"accounts": report.get("market_accounts"), "risk": data.get("risk"), "closed_trades_n": data.get("closed_trades_n")}) + '</pre></section>'
    return out

def validate_event(event):
    if event.get("kind") != "binance_paper" or not event.get("cycle_id"):
        raise ValueError("Invalid Binance ledger event")
    s = event["state"]
    if s["version"] != 1 or set(s["accounts"]) != set(MARKETS):
        raise ValueError("Unknown Binance paper state")
    ms(event["at"])
    json.dumps(s, allow_nan=False)
    for market in MARKETS:
        positive(s["accounts"][market]["initial"])
        number(s["accounts"][market]["cash"])
    if len(s["used"]) != len(set(s["used"])):
        raise ValueError("Duplicate Binance fill identity")
    for p in s["positions"].values():
        if p["market"] not in MARKETS or p["id"] not in s["used"]:
            raise ValueError("Invalid Binance position")
        positive(p["size"])
        positive(p["entry_price"])
