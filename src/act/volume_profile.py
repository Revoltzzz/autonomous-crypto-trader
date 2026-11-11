"""Offline candle-derived volume-at-price approximation; PAPER confirmation only."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from act.collectors.base import CollectorError
from act.collectors.geckoterminal import GeckoTerminalCollector


class VolumeProfileSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOLUME_PROFILE_", extra="ignore",
                                      frozen=True, allow_inf_nan=False)
    timeframe: Literal["15m", "5m"] = "15m"
    fallback_timeframe: Literal["5m"] = "5m"
    value_area_pct: float = Field(default=70, gt=0, le=100)
    min_candles: int = Field(default=12, ge=1, le=1000)
    bins: int = Field(default=24, ge=1, le=200)
    # Mandatory safety invariant: an env override cannot bypass missing OHLCV.
    required_for_buy: bool = True

    @field_validator("required_for_buy")
    @classmethod
    def require_profile(cls, value):
        if not value:
            raise ValueError("Volume profile is mandatory for BUY; false is not permitted")
        return value


@dataclass(frozen=True)
class VolumeProfile:
    poc: float
    vah: float
    val: float
    value_area_pct: float
    actual_value_area_pct: float
    candle_count: int
    total_volume: float
    bin_volumes: tuple[float, ...]


def validate_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("OHLCV list empty or missing")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise ValueError("OHLCV row must be [timestamp, open, high, low, close, volume]")
        if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not isfinite(x)
               for x in row):
            raise ValueError("OHLCV values must be finite numbers")
        ts, op, hi, lo, cl, vol = row
        if ts < 0 or int(ts) != ts or ts in seen:
            raise ValueError("OHLCV timestamp invalid or duplicate")
        if not 0 < lo <= min(op, cl) <= max(op, cl) <= hi or vol < 0:
            raise ValueError("OHLCV price range or volume invalid")
        seen.add(ts)
        result.append(tuple(row))
    return result


def compute_volume_profile(rows, *, bins=24, value_area_pct=70):
    """Uniform high-low overlap allocation. Ties choose the lower price bin.

    POC is the winning bin midpoint; VAL/VAH are outside bin edges.
    Expand one adjacent bin at a time, choosing the larger volume, ties down.
    This approximates volume-at-price, not buy/sell order flow.
    """
    if isinstance(bins, bool) or not isinstance(bins, int) or not 1 <= bins <= 200:
        raise ValueError("bins must be an integer in [1, 200]")
    if not isfinite(value_area_pct) or not 0 < value_area_pct <= 100:
        raise ValueError("value_area_pct must be in (0, 100]")
    candles = validate_rows(rows)
    low = min(r[3] for r in candles)
    high = max(r[2] for r in candles)
    total = sum(r[5] for r in candles)
    if not isfinite(total) or total <= 0:
        raise ValueError("OHLCV has no positive finite volume")
    if high == low:
        return VolumeProfile(low, high, low, value_area_pct, 100, len(candles), total, (total,))
    width = (high - low) / bins
    if width <= 0 or not isfinite(width):
        raise ValueError("Unrepresentable price bin width")
    volumes = [0.0] * bins
    for _, _, hi, lo, close, volume in candles:
        if hi == lo:
            index = min(bins - 1, max(0, int((close - low) / width)))
            volumes[index] += volume
        else:
            for i in range(bins):
                overlap = max(0.0, min(hi, low + (i + 1) * width) - max(lo, low + i * width))
                volumes[i] += volume * overlap / (hi - lo)
    poc = max(range(bins), key=lambda i: volumes[i])
    left = right = poc
    contained = volumes[poc]
    target = sum(volumes) * value_area_pct / 100
    while contained < target and (left > 0 or right < bins - 1):
        below = volumes[left - 1] if left > 0 else -1
        above = volumes[right + 1] if right < bins - 1 else -1
        if below >= above:
            left -= 1
            contained += volumes[left]
        else:
            right += 1
            contained += volumes[right]
    return VolumeProfile(low + (poc + 0.5) * width, low + (right + 1) * width,
                         low + left * width, value_area_pct,
                         contained / sum(volumes) * 100, len(candles), total, tuple(volumes))


def price_position(price, profile):
    if price < profile["val"]:
        return "below_VAL"
    if price >= profile["vah"]:
        return "at_or_above_VAH"
    return "inside_value_area"


def profile_stop(entry, val, max_fraction):
    if not 0 < val < entry:
        raise ValueError("VAL must be positive and strictly below entry")
    floor = entry * (1 - max_fraction)
    if val < floor:
        return floor, "VAL beyond maximum stop fraction; fraction cap used (stop above VAL)"
    return val, "Stop at VAL"


class VolumeProfileFetcher(GeckoTerminalCollector):
    """One instance per cycle: success and failure cached; shared bounded 429 retry."""
    def __init__(self, *args, config=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = config or VolumeProfileSettings()
        self.cache = {}

    async def profile(self, pool, *, at=None):
        if pool.pool_address in self.cache:
            return self.cache[pool.pool_address]
        cfg = self.config
        at = at or datetime.now(timezone.utc)
        age = (at - pool.first_seen_at).total_seconds() / 3600
        timeframe = cfg.fallback_timeframe if age < 24 else cfg.timeframe
        attempts = []
        result = None
        while True:
            try:
                aggregate = int(timeframe.removesuffix("m"))
                url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/"
                       + pool.pool_address + "/ohlcv/minute")
                payload = await self._payload(url=url, params={"aggregate": aggregate,
                    "limit": 100, "currency": "usd", "token": "base",
                    "include_empty_intervals": "false"})
                raw_rows = payload["data"]["attributes"]["ohlcv_list"]
                rows = [] if raw_rows == [] else validate_rows(raw_rows)
                # Only completed candles; never invent the current candle's final volume.
                end = at.timestamp()
                if any(r[0] > end for r in rows):
                    raise ValueError("OHLCV contains future timestamps")
                rows = [r for r in rows if r[0] + aggregate * 60 <= end]
                if len(rows) < cfg.min_candles:
                    attempts.append(f"{timeframe}: too few completed candles ({len(rows)} < {cfg.min_candles})")
                    if timeframe != cfg.fallback_timeframe:
                        timeframe = cfg.fallback_timeframe
                        continue
                    raise ValueError("Pool too young or insufficient OHLCV history")
                if end - max(r[0] for r in rows) > 3 * aggregate * 60:
                    raise ValueError("OHLCV newest completed candle stale (>3 intervals)")
                vp = compute_volume_profile(rows, bins=cfg.bins, value_area_pct=cfg.value_area_pct)
                result = dict(asdict(vp), status="available", timeframe=timeframe,
                              source_url=url, currency="usd", token="base",
                              oldest_timestamp=min(r[0] for r in rows),
                              newest_timestamp=max(r[0] for r in rows), attempts=attempts,
                              ohlcv_rows=rows, fetched_at=datetime.now(timezone.utc).isoformat(),
                              request_params={"aggregate": aggregate, "limit": 100,
                                              "currency": "usd", "token": "base",
                                              "include_empty_intervals": "false"},
                              evaluation_at=at.isoformat())
                break
            except (CollectorError, ValueError, KeyError, TypeError) as exc:
                result = dict(status="unavailable", reason=str(exc), timeframe=timeframe,
                              attempts=attempts)
                break
        self.cache[pool.pool_address] = result
        return result
