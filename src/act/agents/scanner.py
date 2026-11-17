"""Analysis-only rotating Binance scanner and evidence-based technical agent."""
from datetime import datetime, timezone
import logging
import math
from statistics import mean, pstdev


from act.agents import BudgetExceeded, thaw, utcnow
from act.agents.movers import configured_top_n, rotation_rows, selection_plan
from act.binance_data import BinanceData, BinanceError, GeoBlocked, Malformed, number


LOG = logging.getLogger(__name__)




TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")




def universe(info, tickers, market):
    try:
        meta = {r["symbol"]: r for r in info["symbols"] if r["status"] == "TRADING"
                and ((market == "futures" and r["quoteAsset"] == "USDT" and r["contractType"] == "PERPETUAL")
                     or (market == "spot" and r.get("isSpotTradingAllowed") is True))}
        rows, found = [], set()
        for r in tickers:
            if r["symbol"] in meta and r["symbol"] not in found:
                volume, change = number(r["quoteVolume"]), number(r["priceChangePercent"])
                if volume < 0:
                    raise Malformed("Negative quote volume")
                found.add(r["symbol"])
                rows.append({"market": market, "symbol": r["symbol"],
                             "quote_asset": meta[r["symbol"]]["quoteAsset"],
                             "quote_volume_24h": volume, "change_pct_24h": change})
        # Quote currencies are not USD equivalents. Normalize volume within each.
        for quote in {r["quote_asset"] for r in rows}:
            group = sorted((r for r in rows if r["quote_asset"] == quote),
                           key=lambda r: (r["quote_volume_24h"], r["symbol"]))
            for i, row in enumerate(group, 1):
                row.update(volume_percentile=i / len(group), quote_group_n=len(group))
                row["priority"] = row["volume_percentile"] * (1 + abs(row["change_pct_24h"]) / 100)
        return set(meta), sorted(rows, key=lambda r: (-r["priority"], r["symbol"]))
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




def structure(rows):
    previous, last = rows[-20:-1], rows[-1]
    support, resistance = min(r[3] for r in previous), max(r[2] for r in previous)
    recent, older = rows[-5:], rows[-10:-5]
    rh, rl = max(r[2] for r in recent), min(r[3] for r in recent)
    ph, pl = max(r[2] for r in older), min(r[3] for r in older)
    stats, flags = indicators(rows), []
    if stats["volume_surge"] is not None and stats["volume_surge"] >= 3:
        flags.append("HEURISTIC volume spike >=3x previous19 mean; not manipulation detection")
    move_pct = (last[4] / rows[-2][4] - 1) * 100
    if abs(move_pct) >= max(1, 3 * stats["atr_pct"]):
        flags.append("HEURISTIC abrupt close move >=max(1%,3x ATR%); not manipulation detection")
    return {"support": support, "resistance": resistance, "range_reference_n": len(previous),
            "breakout": last[4] > resistance, "breakdown": last[4] < support,
            "structure": "higher_highs_higher_lows" if rh > ph and rl > pl else "lower_highs_lower_lows" if rh < ph and rl < pl else "mixed_or_flat",
            "structure_window_n": 5, "comparison_windows": 2, "last_move_pct": move_pct,
            "heuristic_risk_flags": flags,
            "evidence": {"last_open_time_ms": last[0], "last_close": last[4],
                         "recent_high": rh, "recent_low": rl, "previous_high": ph, "previous_low": pl}}




def restored_state(store):
    from act.results import load_reports
    reports = load_reports(store)
    if not reports and hasattr(store, "directory"):
        # Dashboard local mode uses UUID per-cycle subdirectories.
        from uuid import UUID
        import json
        try:
            UUID(store.directory.name)
        except ValueError:
            return {}
        paths = sorted(store.directory.parent.glob("*/*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        reports = [json.loads(paths[0].read_text())] if paths else []
    return (reports[0].get("scanner_state") or {}) if reports else {}




def coverage_state(old, symbols):
    previous = old.get("universe")
    return {"universe_size": len(symbols), "universe_diff_baseline_available": previous is not None,
            "instruments_added": len(symbols - set(previous)) if previous is not None else None,
            "instruments_removed": len(set(previous) - symbols) if previous is not None else None}




class MarketScannerAgent:
    def __init__(self, market="futures"):
        if market not in ("spot", "futures"):
            raise ValueError("Unknown market")
        self.market, self.name = market, "scanner_" + market


    def describe(self):
        return "Rotating Binance " + self.market + " public evidence; analysis only"


    async def run(self, context):
        api = BinanceData(context, self.market)
        old = (context.snapshot.get("scanner_state") or {}).get(self.market) or {}
        seen = dict(old.get("seen") or {})
        out = {"market": self.market, "as_of": utcnow(), "status": "success", "candidates": [],
               "errors": [], "state": {"seen": seen, "universe": list(old["universe"]) if old.get("universe") is not None else None},
               "coverage": {"scanned_this_cycle": 0, "scanned_symbols": [], "cumulative_unique": len(seen),
               "universe_size": None, "instruments_added": None, "instruments_removed": None,
               "oldest_data_age_seconds": None, "fetches_used": 0, "weight_used": 0,
               "fetch_budget": context.fetch_limit, "weight_budget": api.weight_limit,
               "state_restore_error": context.snapshot.get("scanner_state_error")}}
        if context.snapshot.get("scanner_state_error"):
            out["status"] = "degraded"
        try:
            info = await api.exchange_info()
            tickers, gap = None, None
            if self.market == "spot":
                top_n = configured_top_n()
                try:
                    tickers = await api.tickers()
                    symbols, ranked = universe(info.data, tickers.data, self.market)
                    eligible = [r["symbol"] for r in tickers.data if r["symbol"] in symbols]
                    if set(eligible) != symbols or len(eligible) != len(set(eligible)):
                        raise Malformed("Incomplete or duplicate Spot ticker universe")
                except (BinanceError, BudgetExceeded) as exc:
                    LOG.exception("SPOT_MOVER_ROTATION_FALLBACK: fresh complete tickers unavailable")
                    gap = {"type": type(exc).__name__, "reason": str(exc)}
                    out["errors"].append(dict(gap, stage="mover_ranking"))
                    out["status"] = "degraded"
                    tickers = None
                    ranked = rotation_rows(info.data)
                    symbols = {r["symbol"] for r in ranked}
            else:
                tickers = await api.tickers()
                symbols, ranked = universe(info.data, tickers.data, self.market)
            out["coverage"].update(coverage_state(old, symbols), ticker_symbols=len(ranked))
            out["state"]["universe"] = sorted(symbols)
            batch = sorted(ranked, key=lambda r: (seen.get(r["symbol"], ""), -r["priority"], r["symbol"]))
            if self.market == "spot":
                batch, out["mover_snapshot"] = selection_plan(
                    ranked, seen, top_n=top_n, cycle_id=context.snapshot.get("cycle_id"),
                    as_of=out["as_of"], provenance=tickers.provenance if tickers else None, gap=gap)
                out["coverage"]["ticker_symbols"] = 0 if gap else len(ranked)
                out["coverage"]["selections"] = []
            needed = 4 if self.market == "futures" else 2
            for row in batch:
                if context.fetch_limit - context.used < needed or api.weight_used + 9 > api.weight_limit:
                    break
                symbol = row["symbol"]
                try:
                    depth, bars = await api.depth(symbol), await api.klines(symbol, "1m")
                    stats = indicators(candles(bars.data, datetime.now(timezone.utc).timestamp()))
                    book = liquidity(depth.data)
                    sources = [info.provenance] + ([tickers.provenance] if tickers else []) + [depth.provenance, bars.provenance]
                    derivatives = {"status": "not_applicable", "reason": "Spot has no funding or open interest"}
                    if self.market == "futures":
                        oi, premium = await api.open_interest(symbol), await api.premium_index(symbol)
                        if oi.data.get("symbol") != symbol or premium.data.get("symbol") != symbol:
                            raise Malformed("Derivative instrument mismatch")
                        derivatives = {"status": "available", "open_interest": number(oi.data["openInterest"]),
                                       "mark_price": number(premium.data["markPrice"]),
                                       "last_funding_rate": number(premium.data["lastFundingRate"]),
                                       "open_interest_time": oi.data.get("time"), "premium_time": premium.data.get("time")}
                        sources += [oi.provenance, premium.provenance]
                    score = (row["volume_percentile"] * stats["atr_pct"] / (1 + book["spread_bps"])
                             if row["volume_percentile"] is not None else None)
                    candidate = dict(row, indicators=stats, liquidity=book, derivatives=derivatives,
                                     score=score, as_of=bars.provenance["fetched_at"], provenance=sources)
                    candidate["reasons"] = [f"24h quote volume {row['quote_volume_24h']} {row['quote_asset']}; quote-group n={row['quote_group_n']}",
                                            f"ATR% {stats['atr_pct']} (n={stats['n']})",
                                            f"Snapshot spread {book['spread_bps']} bps",
                                            "Suitability only; quote-volume percentile within quote group, not expected profit"]
                    if gap:
                        candidate["reasons"] = ["Ticker data gap: quote volume, change and suitability score unavailable"]
                    if self.market == "spot":
                        out["coverage"]["selections"].append({"symbol": symbol, **row["selection"]})
                    out["candidates"].append(candidate)
                    seen[symbol] = bars.provenance["fetched_at"]
                    out["coverage"]["scanned_symbols"].append(symbol)
                except (BinanceError, BudgetExceeded, KeyError, TypeError) as exc:
                    LOG.exception("SCANNER_SYMBOL_FAILED market=%s symbol=%s", self.market, symbol)
                    error = {"symbol": symbol, "type": type(exc).__name__, "reason": str(exc)}
                    if self.market == "spot":
                        error["selection"] = row["selection"]
                    out["errors"].append(error)
                    out["status"] = "degraded"
                    if isinstance(exc, GeoBlocked):
                        out.update(status="failed", error=error, candidates=[])
                    if not isinstance(exc, (Malformed, KeyError, TypeError)):
                        break
        except (BinanceError, BudgetExceeded) as exc:
            LOG.exception("SCANNER_UNAVAILABLE market=%s", self.market)
            out.update(status="failed", candidates=[], error={"type": type(exc).__name__, "reason": str(exc)})
        out["candidates"] = sorted(out["candidates"], key=lambda r: -(r["score"] if r["score"] is not None else -1))[:5]
        if self.market == "spot":
            selections = out["coverage"].get("selections", [])
            out["coverage"].update(
                mover_priority_scanned=sum(s["basis"] != "rotation" for s in selections),
                rotation_scanned=sum(s["basis"] == "rotation" for s in selections))
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
    evidence = [max(-2, min(2, f["momentum_pct"] / max(f["atr_pct"], .01))) for f in frames]
    up = 1 / (1 + math.exp(-mean(evidence)))
    directions = ["up" if f["momentum_pct"] > 0 else "down" if f["momentum_pct"] < 0 else "flat" for f in frames]
    return {"up_score": up, "down_score": 1 - up, "n_timeframes": len(frames),
            "timeframe_directions": dict(zip(TIMEFRAMES, directions)),
            "timeframe_agreement": len(set(directions)) == 1,
            "method": "Uncalibrated logistic momentum/ATR heuristic; NOT a forecast probability",
            "reasons": [f"Momentum/ATR bounded evidence {v}" for v in evidence]}




def challenges(rows):
    ordered, disagreements = sorted(rows, key=lambda r: -r["up_score"]), []
    for rank, row in enumerate(ordered, 1):
        subset_rank = sorted(r["scanner_rank"] for r in ordered).index(row["scanner_rank"]) + 1
        row["ta_rank"] = rank
        if rank != subset_rank:
            disagreements.append({"market": row["market"], "symbol": row["symbol"],
                "scanner_rank": row["scanner_rank"], "ta_subset_rank": rank, "n": len(ordered),
                "reason": "Directional momentum ranking differs from scalping suitability; objectives differ"})
    return ordered, disagreements




class TechnicalAnalysisAgent:
    def __init__(self, market="futures"):
        if market not in ("spot", "futures"):
            raise ValueError("Unknown market")
        self.market, self.name = market, "technical_" + market


    def describe(self):
        return "Five-timeframe " + self.market + " evidence, structure and ranking challenges"


    async def run(self, context):
        scanner = (context.snapshot.get("agents") or {}).get("scanner_" + self.market) or {}
        data = scanner.get("data") or {}
        api = BinanceData(context, self.market)
        out = {"market": self.market, "as_of": utcnow(), "status": "success", "candidates": [],
               "errors": [], "disagreements": [], "requested_candidates": len(data.get("candidates") or []),
               "scanner_status": data.get("status") or scanner.get("status"),
               "scanner_error": thaw(data.get("error") or scanner.get("error"))}
        for rank, row in enumerate(data.get("candidates") or [], 1):
            if context.fetch_limit - context.used < len(TIMEFRAMES):
                break
            try:
                if row.get("market") != self.market:
                    raise Malformed("Cross-market candidate rejected")
                frames, provenance = {}, []
                for interval in TIMEFRAMES:
                    bars = await api.klines(row["symbol"], interval)
                    completed = candles(bars.data, datetime.now(timezone.utc).timestamp())
                    frames[interval] = dict(indicators(completed), market_structure=structure(completed))
                    provenance.append(bars.provenance)
                score = directional_score(list(frames.values()))
                out["candidates"].append(dict(score, market=self.market, symbol=row["symbol"], scanner_rank=rank,
                                               frames=frames, provenance=provenance))
            except (BinanceError, BudgetExceeded) as exc:
                LOG.exception("TECHNICAL_UNAVAILABLE market=%s symbol=%s", self.market, row["symbol"])
                error = {"symbol": row["symbol"], "type": type(exc).__name__, "reason": str(exc)}
                out["errors"].append(error)
                if isinstance(exc, GeoBlocked):
                    out.update(status="failed", error=error, candidates=[])
                    break
        out["candidates"], out["disagreements"] = challenges(out["candidates"])
        out.update(analyzed_count=len(out["candidates"]), fetches_used=context.used,
                   fetch_budget=context.fetch_limit, weight_used=api.weight_used)
        if out["status"] != "failed":
            if not out["candidates"]:
                out.update(status="empty", reason="No fully analyzed candidates; see scanner/access errors and budgets")
            if out["analyzed_count"] < out["requested_candidates"]:
                out["status"] = "degraded"
        return out
