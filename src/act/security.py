"""Cycle security provenance and fail-closed primary-source reconciliation."""
from datetime import datetime, timezone

from act.collectors.base import CollectorError
from act.collectors.rugcheck import RugCheckCollector, parse_report
from act.collectors.solana_rpc import FIELDS, SecuritySettings, SolanaRPCCollector, concentration
from act.domain.models import SecurityAssessment


def reconcile(rug, chain, full=None, config=None):
    cfg = config or SecuritySettings()
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
            ("mint", "mintAuthority", "freezeAuthority", "token", "topHolders")}
        parsed = parse_report(full, "rugcheck-full")
        for field in FIELDS[:2]:
            comparable[field] = getattr(parsed, field)
        # Compare the SAME account-based metric only. RugCheck's aggregate has
        # different N/exclusions and is not silently substituted for this metric.
        try:
            if chain.get("excluded_pool_accounts") and full.get("topHolders"):
                rows = [{"address": h["address"], "amount": str(h["amount"]),
                         "decimals": h["decimals"]} for h in full["topHolders"]]
                rc = concentration({"context": {"slot": None}, "value": rows},
                    str(full["token"]["supply"]), full["token"]["decimals"],
                    set(chain["excluded_pool_accounts"]),
                    set(chain.get("excluded_burn_accounts", [])), cfg.security_top_holders_n)
                comparable[FIELDS[2]] = rc[FIELDS[2]]
        except (CollectorError, KeyError, TypeError, ValueError):
            provenance["rugcheck_concentration_comparison"] = "unverifiable or incompatible snapshot"
    for field in FIELDS:
        onchain = chain.get(field)
        rc = comparable.get(field, original.get(field))
        conflict = rc is not None and onchain is not None and rc != onchain
        if conflict:
            provenance["disagreements"].append(field)
            risks.append("Security disagreement: " + field)
        merged[field] = None if conflict else onchain
        provenance["fields"][field] = {"source": "both" if rc is not None and onchain is not None
            else "onchain" if onchain is not None else "rugcheck" if rc is not None else "missing",
            "rugcheck_value": rc, "onchain_value": onchain,
            "effective_value": merged[field], "disagreement": conflict,
            "verified_primary": merged[field] is not None}
    for field, value in merged.items():
        if value is None:
            risks.append("Unavailable: " + field)
    risks.extend("Onchain unavailable: " + e for e in chain.get("errors", []))
    assessment = SecurityAssessment(**merged, risks=risks,
        source="onchain primary; RugCheck score/LP; see security_evidence")
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

    cfg = config or SecuritySettings()
    stats = {"enabled": cfg.security_onchain_enabled, "budget": cfg.solana_rpc_cycle_budget,
             "rpc_calls": 0, "attempted_candidates": 0, "skipped_candidates": 0,
             "verified_three_fields": 0, "passed_three_fields": 0,
             "disagreement_candidates": 0, "error_candidates": 0,
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
                result, detail = reconcile(assessments.get(pool.mint_address), chain, full, cfg)
                merged[pool.mint_address] = result
                evidence[pool.mint_address] = detail
                for field, item in detail["fields"].items():
                    stats["by_field_source"][field][item["source"]] += 1
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
