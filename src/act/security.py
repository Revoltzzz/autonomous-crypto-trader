"""Cycle security provenance and fail-closed primary-source reconciliation."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from act.collectors.base import CollectorError
from act.collectors.rugcheck import RugCheckCollector, parse_report
from act.collectors.solana_rpc import (BURN_OWNER, FIELDS, SecuritySettings,
                                      SolanaRPCCollector, address, concentration, units)
from act.domain.models import SecurityAssessment


class ConcentrationSettings(SecuritySettings):
    security_concentration_fallback: Literal["rugcheck", "off"] = "rugcheck"
    # Percentage points, not a fraction. Crossing the rejection boundary always conflicts.
    security_concentration_tolerance_pp: float = Field(default=0.5, ge=0, le=1)


def raw_integer(value):
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str):
        units(value)
        return value
    raise CollectorError("RugCheck raw amounts require nonnegative integers, never floats/bools")


def rugcheck_concentration(full, mint, pool, n):
    """Account-based top N, with pool identity corroborated by report market vaults.

    A truncated prefix is sufficient only when it contains N eligible accounts.
    Short lists are NOT assumed complete merely because they contain fewer than 20.
    No provider pct/uiAmount fields enter the arithmetic.
    """
    if not isinstance(full, dict) or full.get("mint") != address(mint):
        raise CollectorError("RugCheck concentration mint mismatch")
    address(pool)
    token = full.get("token") or {}
    supply = raw_integer(token.get("supply"))
    decimals = token.get("decimals")
    if int(supply) <= 0 or token.get("isInitialized") is not True or type(decimals) is not int or not 0 <= decimals <= 18:
        raise CollectorError("RugCheck invalid mint supply/decimals/initialization")
    holders = full.get("topHolders")
    if not isinstance(holders, list) or not holders or len(holders) > 20:
        raise CollectorError("RugCheck missing/invalid topHolders")
    markets = full.get("markets")
    if not isinstance(markets, list):
        raise CollectorError("RugCheck candidate pool identity unavailable")
    pool_accounts = set()
    vault_amounts = {}
    for market in markets:
        if not isinstance(market, dict) or market.get("pubkey") != pool:
            continue
        for side in ("A", "B"):
            if market.get("mint" + side) != mint:
                continue
            account = market.get("liquidity" + side + "Account") or {}
            if account.get("owner") != pool or account.get("mint") != mint:
                raise CollectorError("RugCheck market vault owner/mint mismatch")
            key = address(market.get("liquidity" + side))
            pool_accounts.add(key)
            vault_amounts[key] = raw_integer(account.get("amount"))
    if not pool_accounts:
        raise CollectorError("RugCheck candidate pool token accounts unidentified")
    rows, burn_accounts = [], set()
    for holder in holders:
        if not isinstance(holder, dict):
            raise CollectorError("RugCheck holder must be an object")
        key, owner = address(holder.get("address")), address(holder.get("owner"))
        amount = raw_integer(holder.get("amount"))
        if key in vault_amounts and (owner != pool or int(amount) != int(vault_amounts[key])):
            raise CollectorError("RugCheck holder/vault snapshot mismatch")
        if owner == pool:
            pool_accounts.add(key)
        if owner == BURN_OWNER or key == BURN_OWNER:
            burn_accounts.add(key)
        rows.append({"address": key, "amount": amount, "decimals": holder.get("decimals")})
    eligible = [r for r in rows if r["address"] not in pool_accounts | burn_accounts | {BURN_OWNER}]
    if len(eligible) < n and sum(int(r["amount"]) for r in rows) != int(supply):
        raise CollectorError("RugCheck truncated ranking cannot establish requested eligible top N")
    result = concentration({"context": {"slot": None}, "value": rows}, supply,
                           decimals, pool_accounts, burn_accounts, n)
    result.update({"measure": "top_n_eligible_token_accounts_pct_of_raw_supply",
                   "source": "rugcheck", "ranking_rows": len(rows),
                   "reported_total_holders": full.get("totalHolders"),
                   "detected_at": full.get("detectedAt"),
                   "observed_at": datetime.now(timezone.utc).isoformat(),
                   "snapshot_limit": "Provider report; no onchain slot or freshness guarantee"})
    return result


def reconcile(rug, chain, full=None, config=None, *, mint=None, pool=None):
    cfg = config or ConcentrationSettings()
    fallback = getattr(cfg, "security_concentration_fallback", "rugcheck")
    tolerance = getattr(cfg, "security_concentration_tolerance_pp", 0.5)
    original = dict(rug or {})
    provenance = {"rugcheck_summary": original, "onchain": chain,
                  "fields": {}, "disagreements": []}
    merged = {k: original.get(k) for k in
              ("score", "lp_locked_pct", "mint_authority_revoked",
               "freeze_authority_revoked", "top_holder_concentration_pct")}
    risks = [r for r in original.get("risks", []) if not r.startswith("Unavailable:")]
    comparable = {}
    if full:
        provenance["rugcheck_full"] = {k: full.get(k) for k in
            ("mint", "mintAuthority", "freezeAuthority", "token", "topHolders", "markets", "detectedAt")}
        parsed = parse_report(full, "rugcheck-full")
        for field in FIELDS[:2]:
            comparable[field] = getattr(parsed, field)
        try:
            detail = rugcheck_concentration(full, mint, pool, cfg.security_top_holders_n)
            provenance["rugcheck_concentration"] = detail
            comparable[FIELDS[2]] = detail[FIELDS[2]]
        except (CollectorError, KeyError, TypeError, ValueError, AttributeError) as exc:
            provenance["rugcheck_concentration_error"] = str(exc)
    for field in FIELDS:
        onchain = chain.get(field)
        # Never use the summary's differently-defined concentration aggregate.
        rc = comparable.get(field) if field == FIELDS[2] else comparable.get(field, original.get(field))
        conflict = rc is not None and onchain is not None and rc != onchain
        if field == FIELDS[2] and rc is not None and onchain is not None:
            limit = cfg.security_max_top_holders_pct * 100
            conflict = abs(rc - onchain) > tolerance or ((rc >= limit) != (onchain >= limit))
        if conflict:
            provenance["disagreements"].append(field)
            risks.append("Security disagreement: " + field)
        effective = onchain
        if field == FIELDS[2] and effective is None and fallback == "rugcheck" and cfg.security_onchain_enabled:
            effective = rc
        merged[field] = None if conflict else effective
        if field == FIELDS[2]:
            source = "missing" if merged[field] is None else "onchain" if onchain is not None else "rugcheck"
            provenance["concentration_source"] = source
        else:
            source = "both" if rc is not None and onchain is not None else "onchain" if onchain is not None else "rugcheck" if rc is not None else "missing"
        provenance["fields"][field] = {"source": source,
            "rugcheck_value": rc, "onchain_value": onchain,
            "effective_value": merged[field], "disagreement": conflict,
            "verified_primary": merged[field] is not None and onchain is not None,
            "verified": merged[field] is not None}
        if field == FIELDS[2]:
            provenance["fields"][field].update({"measure": "top_n_eligible_token_accounts_pct_of_raw_supply",
                "tolerance_pp": tolerance, "fallback_policy": fallback})
    for field, value in merged.items():
        if value is None:
            risks.append("Unavailable: " + field)
    risks.extend("Onchain unavailable: " + e for e in chain.get("errors", []))
    assessment = SecurityAssessment(**merged, risks=risks,
        source="onchain primary; exclusion-aware RugCheck concentration fallback; see security_evidence")
    return assessment.model_dump(mode="json"), provenance


def preliminary_skip(pool, cfg, at):
    """Optimization only: the unchanged evaluator still runs every gate later."""
    age = (at - pool.first_seen_at).total_seconds() / 3600
    if pool.liquidity_usd is None or pool.liquidity_usd < cfg.min_liquidity_usd:
        return "liquidity unavailable/below unchanged minimum"
    if pool.volume_24h_usd is None or pool.volume_24h_usd < cfg.min_volume_24h_usd:
        return "volume unavailable/below unchanged minimum"
    if not 0 <= age <= cfg.max_pool_age_hours:
        return "pool age outside unchanged range"
    return None


async def enrich_security(pools, assessments, *, client=None, config=None, strategy_config=None):
    from act.strategy import StrategySettings

    cfg = config or ConcentrationSettings()
    stats = {"enabled": cfg.security_onchain_enabled, "budget": cfg.solana_rpc_cycle_budget,
             "rpc_calls": 0, "attempted_candidates": 0, "skipped_candidates": 0,
             "verified_three_fields": 0, "passed_three_fields": 0,
             "disagreement_candidates": 0, "error_candidates": 0,
             "concentration_by_source": {s: 0 for s in ("onchain", "rugcheck", "missing")},
             "concentration_verified": 0, "concentration_passed": 0, "concentration_failed": 0,
             "by_field_source": {f: {s: 0 for s in ("onchain", "rugcheck", "both", "missing")} for f in FIELDS}}
    evidence = {}
    if not cfg.security_onchain_enabled:
        stats["note"] = "Disabled: unchanged RugCheck-only behavior; missing fields still reject"
        return dict(assessments), evidence, stats
    merged = {}
    at = datetime.now(timezone.utc)
    async with SolanaRPCCollector(client=client, config=cfg) as rpc:
        async with RugCheckCollector(client=client, retries=1) as rug:
            for pool in pools:
                skip = preliminary_skip(pool, strategy_config or StrategySettings(), at)
                full = None
                if skip:
                    chain = {"status": "not_attempted", "errors": [skip]}
                    stats["skipped_candidates"] += 1
                else:
                    stats["attempted_candidates"] += 1
                    chain = await rpc.fetch(pool.mint_address, pool.pool_address)
                    try:
                        url = f"https://api.rugcheck.xyz/v1/tokens/{pool.mint_address}/report"
                        full = await rug.get_json(url)
                        if not isinstance(full, dict) or full.get("mint") != pool.mint_address:
                            raise CollectorError("RugCheck full-report mint mismatch")
                    except Exception as exc:
                        full = None
                        chain.setdefault("errors", []).append("RugCheck full comparison: " + str(exc))
                result, detail = reconcile(assessments.get(pool.mint_address), chain, full, cfg,
                                          mint=pool.mint_address, pool=pool.pool_address)
                merged[pool.mint_address] = result
                evidence[pool.mint_address] = detail
                for field, item in detail["fields"].items():
                    stats["by_field_source"][field][item["source"]] += 1
                source = detail["concentration_source"]
                stats["concentration_by_source"][source] += 1
                value = result[FIELDS[2]]
                stats["concentration_verified"] += value is not None
                stats["concentration_passed"] += value is not None and value < cfg.security_max_top_holders_pct * 100
                stats["concentration_failed"] += value is not None and value >= cfg.security_max_top_holders_pct * 100
                verified = all(result[f] is not None for f in FIELDS)
                stats["verified_three_fields"] += verified
                stats["passed_three_fields"] += bool(verified and result[FIELDS[0]] is True
                    and result[FIELDS[1]] is True
                    and result[FIELDS[2]] < cfg.security_max_top_holders_pct * 100)
                stats["disagreement_candidates"] += bool(detail["disagreements"])
                stats["error_candidates"] += bool(chain.get("errors"))
        stats["rpc_calls"] = rpc.calls
        stats["rpc_events"] = rpc.events
    stats["note"] = "Three fields only, not proof of full strategy eligibility. Unknown never permits BUY."
    return merged, evidence, stats
