"""Offline contracts: projected real browser samples captured 2026-11-10.
Only fields exercised by normalization are retained, never synthetic replacements.
Synthetic variations below are explicitly unit-test inputs, not market evidence.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta

import httpx
import pytest

from act.discovery import (DexAdapter, GeckoAdapter, DiscoverySettings,
                           SOURCES, discover, merge_candidates, normalize_pair,
                           rejection_breakdown)
from act.collectors.base import CollectorError
from act.domain.models import AccountState, SecurityAssessment
from act.strategy import evaluate, StrategySettings

# https://api.dexscreener.com/token-pairs/v1/solana/86eESqktK5qmPyZ5mHPoELR32uj9YYa4uB4iMhp6pump
PAIR = {"chainId": "solana", "dexId": "pumpfun",
        "pairAddress": "8MstpfKrcYQQwNFS7oZSopJ65hDwB1hDB9xc7EEHtwRM",
        "baseToken": {"address": "86eESqktK5qmPyZ5mHPoELR32uj9YYa4uB4iMhp6pump", "name": "Seraph the Neko", "symbol": "Seraph"},
        "quoteToken": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL", "symbol": "SOL"},
        "priceUsd": "0.000006470", "txns": {"h24": {"buys": 803, "sells": 489}},
        "volume": {"h24": 32391.77}, "pairCreatedAt": 1794314323000}
# https://api.dexscreener.com/token-profiles/latest/v1 (Solana projection)
PROFILE = {"chainId": "solana", "tokenAddress": PAIR["baseToken"]["address"]}
# https://api.dexscreener.com/token-boosts/latest/v1 (Solana projection)
BOOST = {"chainId": "solana", "tokenAddress": "EJv8TgQfN6gtYN8Z6KGyBeNPbVYmogmudfxiRA2Fpump", "amount": 100, "totalAmount": 100}
# https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?include=base_token
GT_TRENDING = {"data": [{"type": "pool", "attributes": {
    "address": "5n8V68fCaNTRQvaXzrmBryDe5jFLrgm65pFfsmia6Nro",
    "pool_created_at": "2026-11-05T15:04:33Z", "base_token_price_usd": "0.00097032058359372293189912133113361159230071776916414181264631426",
    "reserve_in_usd": "86414.3146", "volume_usd": {"h24": "570399.112051474"},
    "transactions": {"h24": {"buys": 3630, "sells": 2999}}},
    "relationships": {"base_token": {"data": {"id": "solana_8Mi9E1V2bdPeAaRkR5yxUsAvpzP6UBUEEUrYmgKNpump"}}}}]}
# https://api.geckoterminal.com/api/v2/networks/solana/new_pools?include=base_token
GT_NEW = {"data": [{"type": "pool", "attributes": {
    "address": "43bqWiLXYHMGnFGzujBTK7fmcPkMZh5nw6f8rRnqRNjb", "pool_created_at": "2026-11-10T12:50:42Z",
    "base_token_price_usd": "0.0000084854633524038798466718676525078697395188494946966799704718465",
    "reserve_in_usd": None, "volume_usd": {"h24": "0.2545257177"}, "transactions": {"h24": {"buys": 2, "sells": 0}}},
    "relationships": {"base_token": {"data": {"id": "solana_6HCDnRv5P4xWW1JFRaK7brCQspRnrEJqvzMokd7JGxtb"}}}}]}


def run(coro):
    return asyncio.run(coro)


def test_real_pair_normalization_missing_liquidity_stays_unknown():
    p = normalize_pair(PAIR, SOURCES[2])
    assert p.pool_address == PAIR["pairAddress"]
    assert p.mint_address == PROFILE["tokenAddress"]
    assert p.sources == [SOURCES[2]]
    assert p.liquidity_usd is None
    assert "liquidity_usd" in p.unverified_fields
    assert p.volume_24h_usd == 32391.77
    assert p.first_seen_at.timestamp() == PAIR["pairCreatedAt"] / 1000
    assert p.pair_symbols == ["Seraph", "SOL"]


@pytest.mark.parametrize("source,payload", [(SOURCES[0], GT_NEW), (SOURCES[1], GT_TRENDING)])
def test_real_gecko_contract(source, payload):
    async def check():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
        async with client, GeckoAdapter(source, 10, client=client, retries=0) as adapter:
            p = (await adapter.fetch())[0]
            assert p.sources == [source]
            assert p.pool_address == payload["data"][0]["attributes"]["address"]
            assert p.first_seen_at.tzinfo is not None
    run(check())


@pytest.mark.parametrize("field", ["pairAddress", "pairCreatedAt", "baseToken"])
def test_missing_identity_or_age_never_invented(field):
    row = deepcopy(PAIR)
    row.pop(field)
    with pytest.raises(ValueError):
        normalize_pair(row, SOURCES[2])


def test_non_solana_skipped():
    assert normalize_pair(dict(PAIR, chainId="base"), SOURCES[2]) is None


def test_dedup_canonical_pool_metrics_provenance_and_cap():
    first = normalize_pair(PAIR, SOURCES[0])
    alias = first.model_copy(update={"pool_address": "alternate-pool", "sources": [SOURCES[2]], "liquidity_usd": 999999})
    other = first.model_copy(update={"pool_address": "other-pool", "mint_address": "other-mint", "sources": [SOURCES[3]]})
    rows, unique, duplicate = merge_candidates([other, alias, first], SOURCES, 1)
    assert unique == 2 and len(rows) == 1
    assert rows[0].sources == [SOURCES[0], SOURCES[2]]
    assert rows[0].pool_address == first.pool_address
    assert rows[0].liquidity_usd is None  # never splice alias pool metrics
    assert rows[0].alternate_pool_addresses == ["alternate-pool"]
    assert duplicate[SOURCES[2]] == 1
    assert first.sources == [SOURCES[0]]  # input not mutated


def test_volume_tie_break_deterministic():
    p = normalize_pair(PAIR, SOURCES[2])
    q = p.model_copy(update={"pool_address": "q", "mint_address": "q", "volume_24h_usd": 999999})
    assert merge_candidates([p, q], SOURCES, 1)[0][0].pool_address == "q"
    with pytest.raises(ValueError):
        DiscoverySettings(source_priority="geckoterminal_new")


def test_one_source_down_others_continue_and_all_down_loud():
    async def check(all_down):
        def handler(request):
            path = request.url.path
            if not all_down and path.endswith("new_pools"):
                return httpx.Response(200, json=GT_NEW)
            return httpx.Response(503, json={"error": "offline test failure"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            stats = {}
            call = discover(options={"client": client, "retries": 0}, stats=stats)
            if all_down:
                with pytest.raises(CollectorError, match="All enabled"):
                    await call
            else:
                result = await call
                assert len(result) == 1
                assert stats[SOURCES[0]]["kept_after_cap"] == 1
                assert stats[SOURCES[2]]["status"] == "failed"
                assert stats["totals"]["returned"] == 1
    run(check(False))
    run(check(True))


def test_dex_429_retry_after_and_feed_contract():
    async def check():
        calls, delays = [], []
        def handler(request):
            calls.append(request.url.path)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "7"})
            if "token-profiles" in request.url.path:
                return httpx.Response(200, json=[PROFILE])
            return httpx.Response(200, json=[PAIR])
        async def sleep(delay):
            delays.append(delay)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with DexAdapter(SOURCES[2], 10, 5, client=client, retries=1, sleep=sleep) as adapter:
                rows = await adapter.fetch()
                assert rows[0].mint_address == PROFILE["tokenAddress"]
        assert delays == [7] and len(calls) == 3
    run(check())


def test_evaluator_sources_and_exclusive_rejection_counts():
    at = datetime(2026, 11, 10, 13, tzinfo=timezone.utc)
    p = normalize_pair(PAIR, SOURCES[2]).model_copy(update={"first_seen_at": at-timedelta(hours=73), "liquidity_usd": 20000, "unverified_fields": []})
    account = AccountState(equity=10000, cash=10000, day_start_equity=10000, peak_equity=10000)
    security = SecurityAssessment(score=1, lp_locked_pct=100, mint_authority_revoked=True, freeze_authority_revoked=True, top_holder_concentration_pct=1, source="synthetic test")
    age = evaluate(p, security, cycle_id="test", account=account, at=at).model_dump(mode="json")
    missing = evaluate(p.model_copy(update={"first_seen_at": at}), None, cycle_id="test", account=account, at=at).model_dump(mode="json")
    both = evaluate(p, None, cycle_id="test", account=account, at=at).model_dump(mode="json")
    assert age["sources"] == [SOURCES[2]] and age["decision"] == "REJECT"
    counts = rejection_breakdown([age, missing, both])
    assert counts["only_pool_age"] == 1
    assert counts["only_missing_rugcheck_authority"] == 1
    assert counts["by_reason"]["pool_age"] == 2
    assert counts["by_reason"]["missing:score"] == 2
    assert StrategySettings().max_pool_age_hours == 72
    assert StrategySettings().max_rugcheck_score == 500

def test_boost_contract_empty_pair_is_not_fabricated():
    async def check():
        def handler(request):
            if "token-boosts" in request.url.path:
                return httpx.Response(200, json=[BOOST])
            assert request.url.path.endswith(BOOST["tokenAddress"])
            return httpx.Response(200, json=[])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with DexAdapter(SOURCES[3], 10, 5, client=client, retries=0) as adapter:
                assert await adapter.fetch() == []
    run(check())


def test_disabled_sources_and_cap_stats():
    async def check():
        payload = deepcopy(GT_NEW)
        other = deepcopy(payload["data"][0])
        other["attributes"]["address"] = "synthetic-other-pool"
        other["relationships"]["base_token"]["data"]["id"] = "solana_synthetic-other-mint"
        payload["data"].append(other)
        def handler(request):
            return httpx.Response(200, json=payload)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            stats = {}
            rows = await discover(options={"client": client, "retries": 0}, config=DiscoverySettings(enable_dexscreener=False, max_candidates=1), stats=stats)
            assert len(rows) == 1
            assert stats[SOURCES[2]]["status"] == "skipped"
            assert stats["totals"] == dict(returned=4, unique=2, duplicates=2, kept=1, removed_by_cap=1, cap=1)
            assert rows[0].sources == list(SOURCES[:2])
    run(check())
