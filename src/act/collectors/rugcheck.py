"""Defensive RugCheck summary parser; absence never implies safety."""
import math
import re

from act.collectors.base import BaseCollector, CollectorError
from act.domain.models import SecurityAssessment

BASE = "https://api.rugcheck.xyz/v1/tokens"


def number(value, maximum=None):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        if not math.isfinite(result) or result < 0:
            return None
        return result if maximum is None or result <= maximum else None
    except (TypeError, ValueError):
        return None


def parse_report(data: dict, source: str) -> SecurityAssessment:
    if not isinstance(data, dict):
        raise CollectorError("RugCheck report must be an object")
    risks = []
    raw_risks = data.get("risks")
    if isinstance(raw_risks, list):
        for risk in raw_risks:
            if isinstance(risk, dict):
                risks.append(str(risk.get("name") or risk.get("description") or "Unnamed risk"))
            elif isinstance(risk, str):
                risks.append(risk)
    else:
        risks.append("Unavailable: risks")
    token = data.get("token")
    token = token if isinstance(token, dict) else {}
    values = {}
    for output, key in (("mint_authority_revoked", "mintAuthority"),
                        ("freeze_authority_revoked", "freezeAuthority")):
        container = data if key in data else token
        authority = container.get(key)
        values[output] = (authority is None) if key in container else None
        if authority is not None and not isinstance(authority, str):
            values[output] = None
    # Use explicit percentage fields only, never infer 100% from missing markets.
    values["lp_locked_pct"] = number(data.get("lpLockedPct"), 100)
    values["top_holder_concentration_pct"] = number(data.get("topHolderConcentrationPct"), 100)
    holders = data.get("topHolders")
    if values["top_holder_concentration_pct"] is None and isinstance(holders, list) and holders:
        percentages = [number(h.get("pct"), 100) if isinstance(h, dict) else None for h in holders]
        if all(p is not None for p in percentages) and sum(percentages) <= 100:
            values["top_holder_concentration_pct"] = sum(percentages)
    values["score"] = number(data.get("score"))
    for key, value in values.items():
        if value is None:
            risks.append(f"Unavailable: {key}")
    return SecurityAssessment(**values, risks=risks, source=source)


class RugCheckCollector(BaseCollector):
    async def fetch(self, mint: str) -> SecurityAssessment:
        if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", mint):
            raise ValueError("Expected a Solana base58 mint address")
        url = f"{BASE}/{mint}/report/summary"
        return parse_report(await self.get_json(url), url)
