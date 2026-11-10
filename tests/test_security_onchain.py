"""Offline security contracts. Captured projections are labeled; edge cases synthetic."""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from act.collectors.base import CollectorError
from act.collectors.rugcheck import parse_report
from act.collectors.solana_rpc import (BURN_OWNER, FIELDS, SecuritySettings,
    SolanaRPCCollector, concentration, mint_evidence, owned_accounts)
from act.discovery import rejection_breakdown
from act.domain.models import SecurityAssessment, TokenDiscovery
from act.security import enrich_security, reconcile
from act.strategy import evaluate

MINT = "6sGK9xX2wSsMiUTTo3xZBKe7fcydF3rtQnSGtKJMpump"
POOL = "E3rYVe57SbyhvZHZnjwh7L1Wd74vyk5M3CRFkeSj6wdQ"
VAULT = "9LSQizvrMgMeUq5BuZQaNSSwdox2x8NmYvmk6pJKPWhe"
PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
# Real api.mainnet-beta.solana.com getAccountInfo projection observed 2026-11-10.
# Unused lamports/extensions omitted, not a fabricated successful largest-account response.
REAL_MINT = {"context": {"slot": 467750979}, "value": {
    "owner": PROGRAM, "executable": False, "data": {"parsed": {"type": "mint", "info": {
        "mintAuthority": None, "freezeAuthority": None, "isInitialized": True,
        "supply": "1000000000000000", "decimals": 6}}}}}
# Real owner-query projection at slot467750983, same endpoint/date/mint/pool.
REAL_OWNER = {"context": {"slot": 467750983}, "value": [{"pubkey": VAULT, "account": {
    "owner": PROGRAM, "executable": False, "data": {"parsed": {"type": "account", "info": {
        "owner": POOL, "mint": MINT, "tokenAmount": {
            "amount": "1000000000000000", "decimals": 6}}}}}}]}
REAL_429 = {"jsonrpc": "2.0", "error": {"code": 429,
    "message": "Too many requests for a specific RPC call"}, "id": 1}
# Real AICoin summary field projection. Authorities/holders genuinely absent.
REAL_SUMMARY = {"score": 1, "lpLockedPct": 94.6695026022904, "risks": []}


def key(i):
    """Synthetic base58-shaped account identifiers for arithmetic only."""
    return "2" * 30 + "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"[i // 32] + "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"[i % 32]


def largest(amounts):
    return {"context": {"slot": 467750983}, "value": [
        {"address": key(i), "amount": str(amount), "decimals": 6}
        for i, amount in enumerate(amounts)]}


def test_real_mint_and_owner_contracts():
    evidence = mint_evidence(REAL_MINT)
    assert evidence[FIELDS[0]] is True and evidence[FIELDS[1]] is True
    assert evidence["supply"] == "1000000000000000"
    assert owned_accounts(REAL_OWNER, POOL, MINT) == {VAULT}
    wrong = copy.deepcopy(REAL_OWNER)
    wrong["value"][0]["account"]["data"]["parsed"]["info"]["owner"] = BURN_OWNER
    with pytest.raises(CollectorError, match="identity mismatch"):
        owned_accounts(wrong, POOL, MINT)


@pytest.mark.parametrize("field", ["mintAuthority", "freezeAuthority"])
def test_explicit_null_not_missing_or_active(field):
    mutated = copy.deepcopy(REAL_MINT)
    info = mutated["value"]["data"]["parsed"]["info"]
    info[field] = POOL  # synthetic active authority mutation of captured fixture
    assert mint_evidence(mutated)["mint_authority_revoked" if field == "mintAuthority" else "freeze_authority_revoked"] is False
    del info[field]
    with pytest.raises(CollectorError, match="Missing explicit"):
        mint_evidence(mutated)


def test_invalid_program_zero_supply_and_wrong_type_reject():
    for path, value in [("owner", "unknown"), ("supply", "0"), ("type", "account")]:
        changed = copy.deepcopy(REAL_MINT)
        if path == "owner":
            changed["value"][path] = value
        elif path == "type":
            changed["value"]["data"]["parsed"][path] = value
        else:
            changed["value"]["data"]["parsed"]["info"][path] = value
        with pytest.raises(CollectorError):
            mint_evidence(changed)


def test_concentration_excludes_pool_and_incinerator_without_reducing_supply():
    rows = largest([400, 200, 150, 100, 50])
    rows["value"][1]["address"] = BURN_OWNER
    value = concentration(rows, "1000", 6, {key(0)}, set(), 2)
    assert value[FIELDS[2]] == 25
    assert value["numerator_raw"] == "250" and value["denominator_raw"] == "1000"
    assert value["eligible_accounts_summed"] == 2
    assert concentration(rows, "1000", 6, {key(0)}, {key(2)}, 2)[FIELDS[2]] == 15


def test_unidentified_pool_and_truncation_are_unknown_not_zero():
    with pytest.raises(CollectorError, match="unidentified"):
        concentration(largest([1]), "100", 6, set(), set(), 10)
    with pytest.raises(CollectorError, match="truncation"):
        concentration(largest([1] * 20), "100", 6, {key(i) for i in range(11)}, set(), 10)


@pytest.mark.parametrize("bad", ["duplicate", "decimals", "float", "unsorted", "excess"])
def test_invalid_largest_arrays_fail_closed(bad):
    rows = largest([30, 20])
    if bad == "duplicate":
        rows["value"][1]["address"] = key(0)
    elif bad == "decimals":
        rows["value"][0]["decimals"] = 9
    elif bad == "float":
        rows["value"][0]["amount"] = 30.0
    elif bad == "unsorted":
        rows["value"].reverse()
    with pytest.raises(CollectorError):
        concentration(rows, "10" if bad == "excess" else "100", 6, {VAULT}, set(), 10)


def test_rugcheck_summary_gap_and_full_explicit_null_regression():
    summary = parse_report(REAL_SUMMARY, "captured summary")
    assert all(getattr(summary, f) is None for f in FIELDS)
    # Synthetic full shape with captured authority values, not an entire raw response.
    full = parse_report(dict(REAL_SUMMARY, token={"mintAuthority": None,
        "freezeAuthority": None}, topHolders=[{"pct": 12}]), "full shape")
    assert full.mint_authority_revoked is True and full.freeze_authority_revoked is True
    assert full.top_holder_concentration_pct == 12


def test_primary_never_falls_back_and_disagreement_is_explicit():
    rug = dict(score=1, lp_locked_pct=90, mint_authority_revoked=True,
        freeze_authority_revoked=True, top_holder_concentration_pct=20)
    missing, detail = reconcile(rug, {"errors": ["RPC unavailable"]})
    assert all(missing[f] is None for f in FIELDS)
    assert all(not detail["fields"][f]["verified_primary"] for f in FIELDS)
    chain = {FIELDS[0]: False, FIELDS[1]: True, FIELDS[2]: 20}
    merged, detail = reconcile(rug, chain)
    assert merged[FIELDS[0]] is None
    assert detail["disagreements"] == [FIELDS[0]]
    assert "Security disagreement: " + FIELDS[0] in merged["risks"]


def candidate():
    return TokenDiscovery(mint_address=MINT, pool_address=POOL, symbol="TEST",
        first_seen_at=datetime.now(timezone.utc) - timedelta(hours=1), price_usd=2,
        liquidity_usd=10000, volume_24h_usd=25000, buys_24h=110, sells_24h=100,
        source_url="https://example.org/offline")


def test_evaluator_authority_threshold_missing_disagreement_and_breakdown(monkeypatch):
    monkeypatch.setenv("SECURITY_ONCHAIN_ENABLED", "true")
    monkeypatch.setenv("SECURITY_MAX_TOP_HOLDERS_PCT", "0.50")
    good = dict(score=1, lp_locked_pct=80, mint_authority_revoked=True,
        freeze_authority_revoked=True, top_holder_concentration_pct=49, source="offline onchain")
    def screen(fields):
        return evaluate(candidate(), SecurityAssessment(**fields), cycle_id="offline",
            account=object(), screening_only=True).model_dump(mode="json")
    assert screen(good)["decision"] == "WATCH"
    too_high = screen(dict(good, top_holder_concentration_pct=50))
    active = screen(dict(good, mint_authority_revoked=False))
    unknown = screen(dict(good, freeze_authority_revoked=None))
    conflict = screen(dict(good, risks=["Security disagreement: mint_authority_revoked"]))
    assert all(x["decision"] == "REJECT" for x in [too_high, active, unknown, conflict])
    stats = rejection_breakdown([too_high, active, unknown, conflict])
    assert stats["missing_security_data"] == 1 and stats["failed_security_check"] == 3
    assert stats["by_reason"]["top_holder_concentration"] == 1
    assert stats["by_reason"]["security_disagreement"] == 1


def test_rpc_budget_cache_and_read_only():
    async def task():
        requests = []
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"result": REAL_MINT})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            async with SolanaRPCCollector(client=client,
                    config=SecuritySettings(solana_rpc_cycle_budget=1)) as rpc:
                assert await rpc.rpc("getAccountInfo", [MINT]) == REAL_MINT
                assert await rpc.rpc("getAccountInfo", [MINT]) == REAL_MINT
                with pytest.raises(CollectorError, match="budget"):
                    await rpc.rpc("getAccountInfo", [POOL])
                with pytest.raises(CollectorError, match="forbidden"):
                    await rpc.rpc("sendTransaction", [])
                assert rpc.calls == len(requests) == 1
    asyncio.run(task())


def test_observed_429_is_loud_bounded_and_failure_cached():
    async def task():
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(429, json=REAL_429, headers={"Retry-After": "999"}))) as client:
            async with SolanaRPCCollector(client=client, sleep=sleep) as rpc:
                for _ in range(2):
                    with pytest.raises(CollectorError, match="429"):
                        await rpc.rpc("getTokenLargestAccounts", [MINT])
                assert rpc.calls == 2 and sleeps == [10]
                assert all(e["http_status"] == 429 for e in rpc.events)
    asyncio.run(task())


def test_fetch_preserves_verified_authorities_when_pool_unidentified():
    async def task():
        def respond(request):
            method = json.loads(request.content)["method"]
            result = REAL_MINT if method == "getAccountInfo" else {
                "context": {"slot": 467750983}, "value": []}
            return httpx.Response(200, json={"result": result})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            async with SolanaRPCCollector(client=client) as rpc:
                evidence = await rpc.fetch(MINT, POOL)
                assert evidence[FIELDS[0]] is True and evidence[FIELDS[1]] is True
                assert FIELDS[2] not in evidence and "unidentified" in evidence["errors"][0]
                assert rpc.calls == 2
    asyncio.run(task())


def test_enrichment_skip_disabled_and_enabled_offline():
    async def task():
        requests = []
        def respond(request):
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=dict(REAL_SUMMARY, mint=MINT,
                    token={"mintAuthority": None, "freezeAuthority": None}))
            method = json.loads(request.content)["method"]
            result = REAL_MINT if method == "getAccountInfo" else {"context": {"slot": 467750983}, "value": []}
            return httpx.Response(200, json={"result": result})
        assessment = parse_report(REAL_SUMMARY, "captured").model_dump(mode="json")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            disabled, _, stats = await enrich_security([candidate()], {MINT: assessment}, client=client,
                config=SecuritySettings(security_onchain_enabled=False))
            assert disabled[MINT][FIELDS[0]] is None and not requests and stats["rpc_calls"] == 0
            skipped = candidate().model_copy(update={"liquidity_usd": 1})
            _, _, stats = await enrich_security([skipped], {MINT: assessment}, client=client)
            assert stats["skipped_candidates"] == 1 and not requests
            merged, evidence, stats = await enrich_security([candidate()], {MINT: assessment}, client=client)
            assert stats["rpc_calls"] == 2 and stats["verified_three_fields"] == 0
            assert merged[MINT][FIELDS[0]] is True and merged[MINT][FIELDS[2]] is None
            assert evidence[MINT]["fields"][FIELDS[0]]["source"] == "both"
    asyncio.run(task())


@pytest.mark.parametrize("kwargs", [{"solana_rpc_url": "https://example.com/key"},
    {"security_top_holders_n": 21}, {"security_max_top_holders_pct": float("nan")},
    {"solana_rpc_cycle_budget": 0}, {"solana_rpc_retries": 3}])
def test_config_rejects_unverified_endpoints_and_unbounded_settings(kwargs):
    with pytest.raises(ValidationError):
        SecuritySettings(**kwargs)
