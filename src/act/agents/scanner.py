"""Analysis-only rotating Binance scanner and evidence-based technical agent."""
from datetime import datetime, timezone
import logging
import math
from statistics import mean, pstdev

from act.agents import BudgetExceeded, utcnow
from act.binance_data import BinanceData, BinanceError, GeoBlocked, Malformed, number

LOG = logging.getLogger(__name__)


def universe(info, tickers):
    try:
        symbols = {r["symbol"] for r in info["symbols"]
                   if r["quoteAsset"] == "USDT" and r["contractType"] == "PERPETUAL"
                   and r["status"] == "TRADING"}
        rows = []
        for r in tickers:
            if r["symbol"] in symbols:
                volume = number(r["quoteVolume"])
                volatility = abs(number(r["priceChangePercent"]))
                if volume < 0:
                    raise Malformed("Negative quote volume")
                rows.append({"symbol": r["symbol"], "quote_volume_24h": volume,
                             "change_pct_24h": number(r["priceChangePercent"]),
                             "priority": math.log1p(volume) * (1 + volatility / 100)})
        return symbols, sorted(rows, key=lambda r: (-r["priority"], r["symbol"]))
    except (KeyError, TypeError):
        raise Malformed("Invalid exchangeInfo or ticker schema") from None


def candles(data, as_of):
    try:
        rows = []
        for r in data:
            if number(r[6]) >= as_of * 1000:
                continue  # exclude incomplete candles
            o, h, low, c, v = [number(r[i]) for i in (1, 2, 3, 4, 5)]
            if min(o, h, low, c) <= 0 or v < 0 or h < max(o, c, low) or low > min(o, c):
                raise Malformed("Invalid OHLCV values")
            rows.append((number(r[0]), o, h, low, c, v))
        if len(rows) < 20 or any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
            raise Malformed("Need at least 20 ordered completed candles")
        return rows
    except (IndexError, TypeError):
        raise Malformed("Invalid kline schema") from None


def indicators(rows):
    closes = [r[4] for r in rows]
    returns = [(b / a - 1) for a, b in zip(closes, closes[1:])]
    tr = [max(r[2] - r[3], abs(r[2] - p[4]), abs(r[3] - p[4]))
          for p, r in zip(rows, rows[1:])][-14:]
    baseline = mean(r[5] for r in rows[-20:-1])
    return {"n": len(rows), "momentum_pct": (closes[-1] / closes[-6] - 1) * 100,
            "realized_vol_pct": pstdev(returns) * 100,
            "atr14": mean(tr), "atr_pct": mean(tr) / closes[-1] * 100,
            "volume_surge": rows[-1][5] / baseline if baseline else None,
            "range_low": min(r[3] for r in rows[-20:]),
            "range_high": max(r[2] for r in rows[-20:]),
            "range_position": ((closes[-1] - min(r[3] for r in rows[-20:])) /
                               (max(r[2] for r in rows[-20:]) - min(r[3] for r in rows[-20:]))
                               if max(r[2] for r in rows[-20:]) > min(r[3] for r in rows[-20:]) else .5),
            "last_close": closes[-1],
            "volume_profile": {"method": "candle-volume by close, not trades at price",
                               "n": len(rows), "base_volume": sum(r[5] for r in rows),
                               "volume_weighted_close": (sum(r[4] * r[5] for r in rows) /
                                                         sum(r[5] for r in rows)
                                                         if sum(r[5] for r in rows) else None)}}


def liquidity(data):
    try:
        bids = [(number(p), number(q)) for p, q in data["bids"]]
        asks = [(number(p), number(q)) for p, q in data["asks"]]
        if not bids or not asks or any(p <= 0 or q < 0 for p, q in bids + asks):
            raise Malformed("Empty or invalid depth")
        bid, ask = max(p for p, _ in bids), min(p for p, _ in asks)
        if ask < bid:
            raise Malformed("Crossed depth snapshot")
        mid = (bid + ask) / 2
        return {"spread_bps": (ask - bid) / mid * 10000,
                "bid_notional": sum(p * q for p, q in bids),
                "ask_notional": sum(p * q for p, q in asks),
                "n_bid_levels": len(bids), "n_ask_levels": len(asks),
                "method": "top-20 snapshot proxy, not executable liquidity"}
    except (KeyError, TypeError, ValueError):
        raise Malformed("Invalid depth schema") from None


def restored_state(store):
    """Newest report is authoritative, including its last-known rotation checkpoint."""
    from act.results import load_reports
    reports = load_reports(store)
    return (reports[0].get("scanner_state") or {"seen": {}}) if reports else {"seen": {}}


class MarketScannerAgent:
    name = "scanner"

    def describe(self):
        return "Binance USDT-M rotating public-data evidence; no trading signals"

    async def run(self, context):
        api = BinanceData(context)
        old = context.snapshot.get("scanner_state") or {}
        seen = dict(old.get("seen") or {})
        out = {"as_of": utcnow(), "status": "success", "candidates": [], "errors": [],
               "state": {"seen": seen}, "coverage": {"scanned_this_cycle": 0,
               "scanned_symbols": [], "cumulative_unique": len(seen), "universe_size": None,
               "oldest_data_age_seconds": None, "fetches_used": 0, "weight_used": 0,
               "fetch_budget": context.fetch_limit, "weight_budget": api.weight_limit,
               "state_restore_error": context.snapshot.get("scanner_state_error")}}
        if context.snapshot.get("scanner_state_error"):
            out["status"] = "degraded"
        try:
            info = await api.exchange_info()
            tickers = await api.tickers()
            symbols, ranked = universe(info.data, tickers.data)
            out["coverage"].update(universe_size=len(symbols), ticker_symbols=len(ranked))
            # Least recently completed first; priority breaks ties, so no tail starvation.
            batch = sorted(ranked, key=lambda r: (seen.get(r["symbol"], ""), -r["priority"]))
            for row in batch:
                if context.fetch_limit - context.used < 2 or api.weight_used + 7 > api.weight_limit:
                    break
                symbol = row["symbol"]
                try:
                    depth = await api.depth(symbol)
                    bars = await api.klines(symbol, "1m")
                    stats = indicators(candles(bars.data, datetime.now(timezone.utc).timestamp()))
                    book = liquidity(depth.data)
                    score = (math.log1p(row["quote_volume_24h"]) * stats["atr_pct"] /
                             (1 + book["spread_bps"]))
                    candidate = dict(row, indicators=stats, liquidity=book, score=score,
                                     as_of=bars.provenance["fetched_at"],
                                     provenance=[info.provenance, tickers.provenance,
                                                 depth.provenance, bars.provenance])
                    candidate["reasons"] = [f"24h quote volume {row['quote_volume_24h']}",
                                            f"ATR% {stats['atr_pct']} (n={stats['n']})",
                                            f"Snapshot spread {book['spread_bps']} bps",
                                            "Ranking is suitability evidence, not expected profit"]
                    out["candidates"].append(candidate)
                    seen[symbol] = bars.provenance["fetched_at"]
                    out["coverage"]["scanned_symbols"].append(symbol)
                except (BinanceError, BudgetExceeded) as exc:
                    LOG.exception("SCANNER_SYMBOL_FAILED symbol=%s", symbol)
                    out["errors"].append({"symbol": symbol, "type": type(exc).__name__, "reason": str(exc)})
                    out["status"] = "degraded"
                    if not isinstance(exc, Malformed):
                        break
        except (BinanceError, BudgetExceeded) as exc:
            LOG.exception("SCANNER_UNAVAILABLE")
            out.update(status="failed", error={"type": type(exc).__name__, "reason": str(exc)})
        out["candidates"] = sorted(out["candidates"], key=lambda r: -r["score"])[:5]
        covered = out["coverage"]["scanned_symbols"]
        age = max((max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds())
                   for at in seen.values()), default=None)
        out["coverage"].update(scanned_this_cycle=len(covered), cumulative_unique=len(seen),
                               oldest_data_age_seconds=age, fetches_used=context.used,
                               weight_used=api.weight_used, observed_weight_1m=api.header_weight)
        if not covered and out["status"] == "success":
            out.update(status="empty", reason="No completed symbol scans within available data/budgets")
        return out


def directional_score(frames):
    # Bounded logistic transform is a heuristic probability-like score, NOT calibrated confidence.
    evidence = [max(-2, min(2, f["momentum_pct"] / max(f["atr_pct"], .01))) for f in frames]
    up = 1 / (1 + math.exp(-mean(evidence)))
    return {"up_score": up, "down_score": 1 - up, "n_timeframes": len(frames),
            "method": "Uncalibrated logistic momentum/ATR heuristic; not a forecast probability",
            "reasons": [f"Momentum/ATR bounded evidence {v}" for v in evidence]}


class TechnicalAnalysisAgent:
    name = "technical"

    def describe(self):
        return "Completed 1m/5m/15m evidence and explicit scanner-ranking challenges"

    async def run(self, context):
        scanner = (context.snapshot.get("agents") or {}).get("scanner") or {}
        data = scanner.get("data") or {}
        api = BinanceData(context)
        out = {"as_of": utcnow(), "status": "success", "candidates": [], "errors": [],
               "disagreements": [], "requested_candidates": len(data.get("candidates") or [])}
        for rank, row in enumerate(data.get("candidates") or [], 1):
            if context.fetch_limit - context.used < 3:
                break
            try:
                frames, provenance = {}, []
                for interval in ("1m", "5m", "15m"):
                    bars = await api.klines(row["symbol"], interval)
                    frames[interval] = indicators(candles(bars.data, datetime.now(timezone.utc).timestamp()))
                    provenance.append(bars.provenance)
                score = directional_score(list(frames.values()))
                out["candidates"].append(dict(score, symbol=row["symbol"], scanner_rank=rank,
                                               frames=frames, provenance=provenance))
            except (BinanceError, BudgetExceeded) as exc:
                LOG.exception("TECHNICAL_UNAVAILABLE symbol=%s", row["symbol"])
                out["errors"].append({"symbol": row["symbol"], "type": type(exc).__name__, "reason": str(exc)})
                if isinstance(exc, GeoBlocked):
                    break
        ordered = sorted(out["candidates"], key=lambda r: -r["up_score"])
        for rank, row in enumerate(ordered, 1):
            # Compare ranks within the actually analyzed subset, never unscanned candidates.
            subset_rank = sorted(r["scanner_rank"] for r in ordered).index(row["scanner_rank"]) + 1
            row["ta_rank"] = rank
            if rank != subset_rank:
                out["disagreements"].append({"symbol": row["symbol"], "scanner_rank": row["scanner_rank"],
                    "ta_subset_rank": rank, "n": len(ordered),
                    "reason": "Directional momentum ranking differs from scalping suitability; objectives differ"})
        out["candidates"] = ordered
        out.update(analyzed_count=len(ordered), fetches_used=context.used, weight_used=api.weight_used)
        if not ordered:
            out.update(status="empty", reason="No fully analyzed candidates; see scanner/access errors and budgets")
        if len(ordered) < out["requested_candidates"]:
            out["status"] = "degraded"
        return out
      
