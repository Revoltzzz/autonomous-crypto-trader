"""Pure Spot selection. No I/O, orders, forecasts or changes to risk policy."""
from copy import deepcopy
import hashlib
import json
import os

from act.binance_data import Malformed

LOSER_LABEL = "RESEARCH / BOUNCE-WATCH ONLY; Spot LONG-ONLY, NO SHORTING"


def configured_top_n():
    key = "BINANCE_SPOT_MOVER_TOP_N"
    try:
        value = int(os.environ.get(key, "1"))
    except ValueError:
        raise ValueError(f"{key} must be an integer in 0..10") from None
    if not 0 <= value <= 10:
        raise ValueError(f"{key} must be an integer in 0..10")
    return value


def rotation_rows(info):
    """Current exchangeInfo only; never invent ticker metrics on fallback."""
    try:
        return [{"market": "spot", "symbol": r["symbol"], "quote_asset": r["quoteAsset"],
                 "priority": 0, "quote_volume_24h": None, "change_pct_24h": None,
                 "volume_percentile": None, "quote_group_n": None}
                for r in info["symbols"]
                if r["status"] == "TRADING" and r.get("isSpotTradingAllowed") is True]
    except (KeyError, TypeError):
        raise Malformed("Invalid Spot exchangeInfo for rotation fallback") from None


def selection_plan(rows, seen, *, top_n, cycle_id, as_of, provenance=None, gap=None):
    """Return detached rows and canonical immutable JSON; never reuse prior rankings.

    Winners and losers are strictly positive/negative, alphabetic tie break.
    Interleave gainer rank 1, loser rank 1, rank 2, ... before rotation.
    The existing scanner alone owns admission against fetch/weight budgets.
    """
    rows = deepcopy(rows)
    rotation = sorted(rows, key=lambda r: (seen.get(r["symbol"], ""), -r["priority"], r["symbol"]))
    gainers = sorted((r for r in rows if not gap and r["change_pct_24h"] > 0),
                     key=lambda r: (-r["change_pct_24h"], r["symbol"]))
    losers = sorted((r for r in rows if not gap and r["change_pct_24h"] < 0),
                    key=lambda r: (r["change_pct_24h"], r["symbol"]))
    def entries(group):
        return [{"symbol": r["symbol"], "rank": i, "priceChangePercent": r["change_pct_24h"]}
                for i, r in enumerate(group, 1)]
    snapshot = {"version": 1, "cycle_id": cycle_id, "as_of": as_of, "top_n": top_n,
                "status": "rotation_fallback" if gap else "available",
                "data_gaps": [gap] if gap else [], "eligible_n": len(rows),
                "ticker_provenance": deepcopy(provenance), "gainers": entries(gainers),
                "losers": entries(losers), "loser_label": LOSER_LABEL,
                "tie_break": "symbol ascending", "selection_order": "interleaved ranks then rotation"}
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
    identity = hashlib.sha256(payload.encode()).hexdigest()
    batch, selected = [], set()
    for i in range(top_n):
        for group, basis in ((gainers, "top_gainer"), (losers, "top_loser")):
            if i < len(group):
                row = group[i]
                row["selection"] = {"basis": basis, "rank": i + 1,
                                    "priceChangePercent": row["change_pct_24h"],
                                    "snapshot_sha256": identity}
                if basis == "top_loser":
                    row["research_label"] = LOSER_LABEL
                batch.append(row)
                selected.add(row["symbol"])
    for index, row in enumerate(rotation):
        if row["symbol"] not in selected:
            row["selection"] = {"basis": "rotation", "rotation_index": index,
                                "snapshot_sha256": identity}
            batch.append(row)
    return batch, {"sha256": identity, "payload_json": payload}
