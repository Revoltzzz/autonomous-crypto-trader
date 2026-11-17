"""Decision-only historical replay. No invented executable kline fills.

This foundation is NOT a completed P&L backtester. Existing Binance fills need
contemporaneous depth, not available from historical klines. Missing execution
inputs remain insufficient_data. No interpolation and no ledger interface.
"""
from dataclasses import dataclass
from math import isfinite

from act.research.registry import timestamp


@dataclass(frozen=True)
class Bar:
    opened_ms: int
    closed_ms: int
    close: float

    def __post_init__(self):
        if self.opened_ms > self.closed_ms or not isfinite(self.close) or self.close <= 0:
            raise ValueError("Invalid bar")


def binance_bars(response):
    return tuple(Bar(int(r[0]), int(r[6]), float(r[4])) for r in response.data)


def solana_bars(rows, interval_seconds):
    if interval_seconds <= 0:
        raise ValueError("Positive interval required")
    return tuple(Bar(int(r[0]) * 1000, (int(r[0]) + interval_seconds) * 1000 - 1,
                     float(r[4])) for r in rows)


async def spot_history(api, symbol, interval="1m"):
    """Use existing bounded public layer; no endpoint/key/account additions.

    Existing public layer permits only 60-bar requests, not arbitrary backfill.
    Require the already-supported vision origin rather than change deployment.
    """
    if api.market != "spot" or api.base_url != "https://data-api.binance.vision":
        raise ValueError("Spot vision public layer required")
    return await api.read("klines", {"symbol": symbol, "interval": interval, "limit": 60})


def replay(version, symbol_bars, decision_times, decide, interval_ms):
    """Callback receives ONLY an immutable prefix of completed candle values.

    Callback purity is the caller's responsibility (no external future data).
    Data timestamps are bar completion times, NOT candle open times.
    """
    if interval_ms <= 0:
        raise ValueError("Positive interval required")
    times = [timestamp(t) for t in decision_times]
    if times != sorted(set(times)):
        raise ValueError("Unique chronological decision times required")
    result = {"strategy_version_id": version["id"], "market": version["market"],
              "symbols": sorted(symbol_bars), "decisions": [], "coverage": {},
              "n_trades": 0, "trades": [], "status": "insufficient_data",
              "execution_status": "not_evaluated; no fabricated fills",
              "requested_start": decision_times[0] if times else None,
              "requested_end": decision_times[-1] if times else None}
    if version["market"] == "futures":
        return {**result, "status": "skipped", "reason": "GeoBlocked HTTP 451; no futures backtest"}
    for symbol, values in symbol_bars.items():
        bars = tuple(sorted(values, key=lambda r: r.opened_ms))
        if len({r.opened_ms for r in bars}) != len(bars):
            raise ValueError("Duplicate candles")
        if any(b.closed_ms >= a.opened_ms + interval_ms * 2 for a, b in zip(bars, bars[1:])
               if b.opened_ms == a.opened_ms + interval_ms):
            raise ValueError("Candle duration invalid")
        gaps = [[a.opened_ms + interval_ms, b.opened_ms] for a, b in zip(bars, bars[1:])
                if b.opened_ms != a.opened_ms + interval_ms]
        if any(b.opened_ms - a.opened_ms < interval_ms for a, b in zip(bars, bars[1:])):
            raise ValueError("Overlapping candles")
        result["coverage"][symbol] = {"n_bars": len(bars), "gaps": gaps,
            "first_open_ms": bars[0].opened_ms if bars else None,
            "last_close_ms": bars[-1].closed_ms if bars else None,
            "interpolated": False, "history_scope": "supplied source coverage only"}
        for at, dt in zip(decision_times, times):
            cutoff = int(dt.timestamp() * 1000)
            prefix = tuple(b for b in bars if b.closed_ms <= cutoff)
            result["decisions"].append({"at": at, "symbol": symbol,
                "status": "decision_only" if prefix else "insufficient_data",
                "input_n": len(prefix), "decision": decide(prefix) if prefix else None,
                "strategy_version_id": version["id"], "record_type": "historical_decision_replay"})
    return result


def walk_forward(length, train, holdout):
    """Disjoint chronological train/holdout blocks; no reuse of a holdout."""
    if any(isinstance(n, bool) or not isinstance(n, int) for n in (length, train, holdout)):
        raise ValueError("Integer split sizes required")
    if length < 0 or min(train, holdout) <= 0:
        raise ValueError("Invalid split sizes")
    return [{"in_sample": [i, i + train], "out_of_sample": [i + train, i + train + holdout]}
            for i in range(0, length - train - holdout + 1, train + holdout)]


class HoldoutBook:
    """Record a holdout before evaluation: exceptions do not allow retuning.

    Supply persisted prior keys on restart. This is not a durable database or a
    complete walk-forward optimizer. Persist used keys even if evaluation fails.
    """
    def __init__(self, used=()):
        self._used = set(used)

    @property
    def used(self):
        return tuple(sorted(self._used))

    def evaluate_once(self, key, evaluate):
        if key in self._used:
            raise ValueError("Holdout already consumed")
        self._used.add(key)
        return evaluate()
      
