"""Offline real-response projections and explicitly synthetic mutations; no network.
Captured 2026-11-10 from https://api.rugcheck.xyz/v1/tokens/
B9FgqHbtccsgcL6YPfUhNd81qveNtrTbsfLvcjxtpump/report.
First 11 of the 20 returned holders projected to raw arithmetic fields.
Unused holders/markets/extensions omitted, not fabricated or owner aggregated.
"""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from act.collectors.base import CollectorError
from act.collectors.solana_rpc import BURN_OWNER, FIELDS
from act.domain.models import TokenDiscovery
from act.security import ConcentrationSettings, enrich_security, reconcile, rugcheck_concentration

MINT = "B9FgqHbtccsgcL6YPfUhNd81qveNtrTbsfLvcjxtpump"
POOL = "CmKXFUCW8CSnmqTVHBrK3E6sZk4fLeeB24yia7GyXeWt"
VAULT = "J39A9AP1P2EhY9Zw7JUfe1R55AmMYAa2L5SudfhRiikb"
CAPTURED_ROWS = [
    (VAULT, POOL, 129652213322980),
    ("Czd6YS3PVc6cRJZ9BPFGJRCyfersohXcuPL1g5hzj9kX", "6451zSoUiy4GZeq2vEUHb73LcDJ8eeCHXEaGSqL8K6Fj", 24829319028088),
    ("29PYC7woLT26S64eGSzonh8JD1RmyfDojw67BYLH4yPC", "EiNTxAB4aTv22ZYxJrwNpAPZj7DGes1fCk21EsBGcWLc", 20298673869397),
    ("GTfARNw9Wh9TBE9dfMGiR6aZh7yWvaaSd1Mf8VSx8dig", "AjvJBP8mYqsXb8L1GrpEPG4aWh7zwpxd86hZTDHDqEv5", 17393333081974),
    ("2yVZ6FiHrf76r6mM83zvpKz6QaszpuN9R63ns9rsybiq", "BM9CcyErJcu2mjrFvUsRRrD3snGeHDDVirJLvL6EjvMN", 16745187433988),
    ("66zi5uVJ16xYE6oH6MHmj9MWbH7zGZGUUK8BHB39jtD4", "5B79fMkcFeRTiwm7ehsZsFiKsC7m7n1Bgv9yLxPp9q2X", 16586640189303),
    ("F7MVbggx2H4gffjwXTDfmt6NcUcsucWMGK6VVpvsH3LC", "6F5x9fKqXCfPf8Rufs6ZvLjzPfCicytUgWexzx6LxoDi", 16538052445979),
    ("4nsyXomgyHbVGzbPozHUzT5QbLSuuJ6CA5MokJ6b51vm", "47peszS9ex1BBajKk2mFuz1zBhgWnC1FdytoUo3Ce5Gd", 16022044980781),
    ("716L5akMdBnwSvQFe8hnHjuybBKzitMrgfv1qV3zmgkM", "A1NRonobpzz48sqPCdLYB81pi3yMsuy7brqUgEhwyq16", 15930577010949),
    ("E37Qkf9DfDEy4nKYqfqvTvAiV5FScaUcjVm9fn5N9Qmy", "3h65MmPZksoKKyEpEjnWU2Yk2iYT5oZDNitGy5cTaxoE", 15260745889807),
    ("FtauWoAm38q4z122rbog5ASbcGzUFrWKxN6TUd9MyjfV", "9YZ7LhNsLVp1fuGWCxEikHRPwQKasex76PXKRn7J2uDY", 14534377515048),
]
REAL = {"mint": MINT, "token": {"supply": 982390369623268, "decimals": 6,
    "mintAuthority": None, "freezeAuthority": None, "isInitialized": True},
    "topHolders": [{"address": a, "owner": o, "amount": v, "decimals": 6}
                   for a, o, v in CAPTURED_ROWS], "totalHolders": 6102,
    "detectedAt": "2026-11-10T02:03:35.547615706Z",
    "markets": [{"pubkey": POOL, "mintA": MINT, "liquidityA": VAULT,
        "liquidityAAccount": {"owner": POOL, "mint": MINT, "amount": 129652213322980}}]}
FIELD = FIELDS[2]
SUMMARY = {"score": 1, "lp_locked_pct": 90, "risks": []}


def normalized(full=None, n=10):
    return rugcheck_concentration(REAL if full is None else full, MINT, POOL, n)


def merge(chain=None, full=None, **cfg):
    return reconcile(SUMMARY, chain or {}, REAL if full is None else full,
                     ConcentrationSettings(**cfg), mint=MINT, pool=POOL)


def test_real_account_shape_and_exclusion_math():
    first = REAL["topHolders"][0]
    assert first["address"] == REAL["markets"][0]["liquidityA"]
    assert first["owner"] == POOL and first["address"] != POOL
    result = normalized()
    expected = sum(h["amount"] for h in REAL["topHolders"][1:11])
    assert result["numerator_raw"] == str(expected)
    assert result[FIELD] == expected * 100 / REAL["token"]["supply"]
    assert result["denominator_raw"] == str(REAL["token"]["supply"])
    assert result["eligible_accounts_summed"] == 10
    assert result["measure"] == "top_n_eligible_token_accounts_pct_of_raw_supply"


def test_fallback_primary_tolerance_and_provenance():
    rc = normalized()[FIELD]
    result, evidence = merge()
    assert result[FIELD] == rc and evidence["concentration_source"] == "rugcheck"
    assert evidence["fields"][FIELD]["verified"]
    assert not evidence["fields"][FIELD]["verified_primary"]
    assert result[FIELDS[0]] is None
    result, evidence = merge({FIELD: rc + 0.25})
    assert result[FIELD] == rc + 0.25 and evidence["concentration_source"] == "onchain"
    result, evidence = merge({FIELD: rc + 0.6})
    assert result[FIELD] is None and evidence["concentration_source"] == "missing"
    assert evidence["disagreements"] == [FIELD]
    assert "Security disagreement: " + FIELD in result["risks"]
    assert evidence["fields"][FIELD]["rugcheck_value"] == rc


def test_boundary_crossing_conflicts_even_within_tolerance():
    rc = normalized()[FIELD]
    result, evidence = merge({FIELD: rc + 0.1}, security_max_top_holders_pct=(rc + 0.05) / 100)
    assert result[FIELD] is None and evidence["disagreements"] == [FIELD]


def test_fallback_off_disabled_and_missing_both():
    result, evidence = merge(security_concentration_fallback="off")
    assert result[FIELD] is None and evidence["concentration_source"] == "missing"
    result, _ = merge(security_onchain_enabled=False)
    assert result[FIELD] is None
    for value in ([], None):  # observed empty/null shapes; synthetic mutations of REAL
        full = copy.deepcopy(REAL)
        full["topHolders"] = value
        result, evidence = merge(full=full)
        assert result[FIELD] is None and "Unavailable: " + FIELD in result["risks"]
        assert evidence["rugcheck_concentration_error"]
    result, _ = reconcile(dict(SUMMARY, top_holder_concentration_pct=1), {})
    assert result[FIELD] is None  # summary aggregate is NEVER this metric


def test_burn_and_pool_owner_exclusions_synthetic_mutations():
    full = copy.deepcopy(REAL)
    full["topHolders"][1]["owner"] = BURN_OWNER
    full["topHolders"][2]["address"] = BURN_OWNER
    full["topHolders"][3]["owner"] = POOL
    result = normalized(full, n=2)
    assert result["numerator_raw"] == str(sum(h["amount"] for h in full["topHolders"][4:6]))
    assert result["denominator_raw"] == str(REAL["token"]["supply"])
    with pytest.raises(CollectorError, match="truncated"):
        normalized(full)


@pytest.mark.parametrize("bad", ["short", "unknown_pool", "wrong_mint", "owner", "vault_amount",
    "float", "bool", "negative", "duplicate", "unsorted", "decimals", "zero_supply", "excess"])
def test_invalid_or_incomplete_evidence_rejected(bad):
    full = copy.deepcopy(REAL)
    if bad == "short":
        full["topHolders"] = full["topHolders"][:10]
    elif bad == "unknown_pool":
        full["markets"] = []
    elif bad == "wrong_mint":
        full["mint"] = POOL
    elif bad == "owner":
        del full["topHolders"][1]["owner"]
    elif bad == "vault_amount":
        full["markets"][0]["liquidityAAccount"]["amount"] += 1
    elif bad in ("float", "bool", "negative"):
        full["topHolders"][1]["amount"] = {"float": 1.5, "bool": True, "negative": -1}[bad]
    elif bad == "duplicate":
        full["topHolders"][1]["address"] = full["topHolders"][2]["address"]
    elif bad == "unsorted":
        full["topHolders"][1:3] = list(reversed(full["topHolders"][1:3]))
    elif bad == "decimals":
        full["topHolders"][1]["decimals"] = 9
    elif bad == "zero_supply":
        full["token"]["supply"] = 0
    elif bad == "excess":
        full["token"]["supply"] = 100
    with pytest.raises(CollectorError):
        normalized(full)
    result, evidence = merge(full=full)
    assert result[FIELD] is None and evidence["concentration_source"] == "missing"


def test_complete_supply_short_ranking_synthetic():
    full = copy.deepcopy(REAL)
    full["topHolders"] = full["topHolders"][:3]
    full["token"]["supply"] = sum(h["amount"] for h in full["topHolders"])
    assert normalized(full)["eligible_accounts_summed"] == 2
    with pytest.raises(CollectorError, match="truncated"):
        normalized(n=20)


def test_config_env_and_invalid_values(monkeypatch):
    monkeypatch.setenv("SECURITY_CONCENTRATION_FALLBACK", "off")
    assert ConcentrationSettings().security_concentration_fallback == "off"
    for kwargs in ({"security_concentration_fallback": "anything"},
                   {"security_concentration_tolerance_pp": 2},
                   {"security_concentration_tolerance_pp": float("nan")}):
        with pytest.raises(ValidationError):
            ConcentrationSettings(**kwargs)


def test_rpc_down_fallback_cycle_stats_budget_and_json_offline():
    async def task():
        requests = []
        def respond(request):
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=REAL)
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        pool = TokenDiscovery(mint_address=MINT, pool_address=POOL, symbol="AICoin",
            first_seen_at=datetime.now(timezone.utc) - timedelta(hours=1), price_usd=1,
            liquidity_usd=10000, volume_24h_usd=25000, buys_24h=110, sells_24h=100,
            source_url="https://example.org/offline")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            merged, evidence, stats = await enrich_security([pool], {MINT: SUMMARY}, client=client,
                config=ConcentrationSettings(solana_rpc_cycle_budget=1, solana_rpc_retries=0))
            assert merged[MINT][FIELD] == normalized()[FIELD]
            assert merged[MINT][FIELDS[0]] is None
            assert stats["rpc_calls"] == 1 and stats["concentration_verified"] == 1
            assert stats["concentration_by_source"] == {"onchain": 0, "rugcheck": 1, "missing": 0}
            assert stats["verified_three_fields"] == 0
            assert stats["rpc_events"][0]["http_status"] == 429
            assert len(requests) == 2
            assert json.loads(json.dumps(evidence))[MINT]["concentration_source"] == "rugcheck"
    asyncio.run(task())
