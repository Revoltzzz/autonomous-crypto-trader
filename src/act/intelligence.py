"""Evidence-only intelligence. Pure computation; never consulted by execution."""
from collections import Counter
from datetime import datetime, timezone
from math import isfinite
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from act.volume_profile import validate_rows

VERSION = "ta-v1"
WINDOWS = {"rsi14": 15, "ema9": 9, "ema21": 21, "ema9_slope": 10,
           "ema21_slope": 22, "ema9_vs_ema21": 21, "momentum5": 6,
           "atr14_normalized": 15, "volume_ratio5": 10,
           "drawdown_from_high": 2, "bars_since_poc_touch": 1}


class IntelligenceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTELLIGENCE_", extra="ignore", frozen=True)
    ta_enabled: bool = True
    # No threshold policy exists yet. Enabling gates requires a reviewed future change.
    gates_enabled: Literal[False] = False


def evidence(value=None, reason=None, **metadata):
    if value is not None and not isfinite(value):
        value, reason = None, "nonfinite computation"
    return dict(value=value, available=value is not None, reason=reason, **metadata)


def ema(values, period):
    """SMA-seeded EMA; returns final and preceding EMA (if available)."""
    current = sum(values[:period]) / period
    previous = None
    for value in values[period:]:
        previous = current
        current += (2 / (period + 1)) * (value - current)
    return current, previous


def wilder(values, period=14):
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = (current * (period - 1) + value) / period
    return current


def technical(profile=None, *, enabled=True):
    """Use only the exact completed candles retained by volume-profile collection.

    No resampling, interpolation, zeros or network. Any gap makes this window
    unavailable. Provider order may be descending; calculation is chronological.
    """
    profile = profile or {}
    meta = {key: profile.get(key) for key in
            ("timeframe", "source_url", "fetched_at", "oldest_timestamp", "newest_timestamp")}
    output = dict(version=VERSION, bar_count=0, metadata=meta, indicators={})
    reason = None
    rows = []
    if not enabled:
        reason = "disabled by INTELLIGENCE_TA_ENABLED"
    elif profile.get("status") != "available":
        reason = profile.get("reason", "OHLCV not fetched: unchanged hard-filter preflight")
    else:
        try:
            rows = sorted(validate_rows(profile.get("ohlcv_rows")), key=lambda row: row[0])
            output["bar_count"] = len(rows)
            interval = int(profile["timeframe"].removesuffix("m")) * 60
            if interval <= 0 or any(b[0] - a[0] != interval for a, b in zip(rows, rows[1:])):
                reason = "patchy OHLCV: noncontiguous timestamps"
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            reason = "invalid OHLCV: " + str(exc)
    indicators = output["indicators"]
    for name, minimum in WINDOWS.items():
        indicators[name] = evidence(reason=reason or f"insufficient bars: {len(rows)} < {minimum}",
                                    minimum_bars=minimum, window_bars=len(rows))
    if reason:
        return output
    close = [r[4] for r in rows]

    def put(name, value=None, unavailable=None):
        if len(rows) >= WINDOWS[name]:
            indicators[name] = evidence(value, unavailable, minimum_bars=WINDOWS[name],
                                        window_bars=len(rows))

    if len(rows) >= 15:
        changes = [b - a for a, b in zip(close, close[1:])]
        gain = wilder([max(x, 0) for x in changes])
        loss = wilder([max(-x, 0) for x in changes])
        rsi = None if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
        put("rsi14", rsi, "flat window: RSI undefined" if rsi is None else None)
        ranges = [max(b[2] - b[3], abs(b[2] - a[4]), abs(b[3] - a[4]))
                  for a, b in zip(rows, rows[1:])]
        put("atr14_normalized", wilder(ranges) / close[-1])
    averages = {}
    for period in (9, 21):
        if len(rows) >= period:
            current, previous = ema(close, period)
            averages[period] = current
            put(f"ema{period}", current)
            if previous is not None:
                put(f"ema{period}_slope", current / previous - 1)
    if len(averages) == 2:
        put("ema9_vs_ema21", averages[9] / averages[21] - 1)
    if len(rows) >= 6:
        put("momentum5", close[-1] / close[-6] - 1)
    if len(rows) >= 10:
        earlier = sum(r[5] for r in rows[-10:-5])
        put("volume_ratio5", sum(r[5] for r in rows[-5:]) / earlier if earlier else None,
            "earlier volume is zero" if not earlier else None)
    if len(rows) >= 2:
        put("drawdown_from_high", close[-1] / max(r[2] for r in rows) - 1)
    poc = profile.get("poc")
    touches = [i for i, r in enumerate(rows) if poc is not None and r[3] <= poc <= r[2]]
    put("bars_since_poc_touch", len(rows) - 1 - touches[-1] if touches else None,
        "no candle range touches full-window POC" if not touches else None)
    return output


def candidate_intelligence(pool, profile, markets, *, at, config=None):
    cfg = config or IntelligenceSettings()
    return dict(schema_version=1, observed_at=at.isoformat(), evidence_only=True,
                gates_enabled=False, ta=technical(profile, enabled=cfg.ta_enabled),
                metadata=getattr(pool, "intelligence_metadata", {}) or {
                    "status": "unavailable", "reason": "No DexScreener metadata retained for this candidate"},
                market_context={"scope": "market-wide", "source": "existing CoinGecko collector",
                                "observations": markets, "sentiment": None,
                                "reason": "Price context is not sentiment; no verified token-mention source"})


def attach_intelligence(report, pools, profiles, *, at=None, config=None):
    """Attach after strategy/execution, persist inside existing proposal JSONB."""
    at = at or datetime.now(timezone.utc)
    cfg = config or IntelligenceSettings()
    by_mint = {p.mint_address: p for p in pools}
    available = Counter()
    missing = {name: Counter() for name in WINDOWS}
    count = 0
    for decision in report.get("decisions", []):
        pool = by_mint[decision["mint_address"]]
        block = candidate_intelligence(pool, profiles.get(pool.pool_address),
                                       report.get("markets", []), at=at, config=cfg)
        decision.setdefault("proposal", {})
        decision["proposal"] = dict(decision.get("proposal") or {}, intelligence=block)
        any_available = False
        for name, item in block["ta"]["indicators"].items():
            if item["available"]:
                available[name] += 1
                any_available = True
            else:
                missing[name][item["reason"]] += 1
        count += any_available
    report["intelligence_stats"] = dict(
        candidates=len(report.get("decisions", [])), ta_computed=count,
        available={name: available[name] for name in WINDOWS},
        unavailable={name: dict(reasons) for name, reasons in missing.items()},
        config=cfg.model_dump(), external_sentiment_requests=0,
        sentiment_coverage="unavailable; market price context only")


def intelligence_panel(latest):
    """Escaped, bounded per-candidate evidence with explicit historic unknowns."""
    import json
    from html import escape

    def raw(value):
        return "<pre>" + escape(json.dumps(value, indent=2, allow_nan=False)) + "</pre>"

    output = ['<section id="intelligence"><h2>Intelligence evidence (no gates)</h2>',
              '<p>TA reuses preflight-eligible OHLCV only. Missing is not neutral or zero. '
              'Social links and boosts are metadata, not sentiment. Market context is not token coverage.</p>',
              raw(latest.get("intelligence_stats", {"status": "not recorded for this cycle"}))]
    for decision in latest.get("decisions", [])[:200]:
        block = (decision.get("proposal") or {}).get("intelligence")
        label = str(decision.get("symbol") or "Unknown") + " · " + str(decision.get("mint_address"))
        output.append("<details><summary>" + escape(label) + "</summary>" +
                      raw(block or {"status": "not recorded"}) + "</details>")
    return "".join(output) + "</section>"
