"""Bounded, sequential, keyless discovery. No execution or safety policy here."""
import logging
from datetime import datetime, timezone
from collections import Counter

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from act.collectors.base import BaseCollector, CollectorError
from act.collectors.geckoterminal import GeckoTerminalCollector, URL
from act.domain.models import TokenDiscovery

LOG = logging.getLogger(__name__)
SOURCES = ("geckoterminal_new", "geckoterminal_trending", "dexscreener_profiles", "dexscreener_boosts")
DEX = "https://api.dexscreener.com"


class DiscoverySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DISCOVERY_", extra="ignore", frozen=True)
    max_candidates: int = Field(default=40, ge=1, le=100)
    enable_geckoterminal: bool = True
    enable_dexscreener: bool = True
    geckoterminal_new_limit: int = Field(default=15, ge=1, le=20)
    geckoterminal_trending_limit: int = Field(default=10, ge=1, le=20)
    dexscreener_profiles_limit: int = Field(default=10, ge=1, le=30)
    dexscreener_boosts_limit: int = Field(default=10, ge=1, le=30)
    token_lookup_limit: int = Field(default=5, ge=1, le=10)
    source_priority: str = ",".join(SOURCES)

    @field_validator("source_priority")
    @classmethod
    def valid_priority(cls, value):
        names = value.split(",")
        if len(names) != len(SOURCES) or set(names) != set(SOURCES):
            raise ValueError("source_priority must contain each supported source exactly once")
        return value


class Candidate(TokenDiscovery):
    sources: list[str] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pair_symbols: list[str] = Field(default_factory=list)
    alternate_pool_addresses: list[str] = Field(default_factory=list)
    intelligence_metadata: dict = Field(default_factory=dict)


def normalize_pair(row, source):
    """Only actual Solana base-token pools; no mint-as-pool substitution."""
    if not isinstance(row, dict) or row.get("chainId") != "solana":
        return None
    base = row.get("baseToken") or {}
    quote = row.get("quoteToken") or {}
    pool, mint, created = row.get("pairAddress"), base.get("address"), row.get("pairCreatedAt")
    if not isinstance(pool, str) or not pool or not isinstance(mint, str) or not mint:
        raise ValueError("Missing actual pairAddress/baseToken.address")
    if not isinstance(created, (int, float)) or isinstance(created, bool) or created <= 0:
        raise ValueError("Missing actual pairCreatedAt; cannot invent pool age")
    tx = (row.get("txns") or {}).get("h24") or {}
    fields = dict(name=base.get("name"), symbol=base.get("symbol"),
                  price_usd=row.get("priceUsd"), liquidity_usd=(row.get("liquidity") or {}).get("usd"),
                  volume_24h_usd=(row.get("volume") or {}).get("h24"),
                  buys_24h=tx.get("buys"), sells_24h=tx.get("sells"))
    info = row.get("info") or {}
    pair_evidence = dict(source_url=f"{DEX}/token-pairs/v1/solana/{mint}",
                         observed_at=datetime.now(timezone.utc).isoformat(), pool_address=pool,
                         socials=info.get("socials"), socials_present=bool(info["socials"]) if "socials" in info else None,
                         boosts=row.get("boosts"), note="Links/paid promotion metadata, not sentiment; omitted is unknown.")
    return Candidate(**fields, mint_address=mint, pool_address=pool,
                     first_seen_at=datetime.fromtimestamp(created / 1000, timezone.utc),
                     source_url=f"https://dexscreener.com/solana/{pool}", sources=[source],
                     pair_symbols=[x for x in (base.get("symbol"), quote.get("symbol")) if x],
                     intelligence_metadata={f"pair:{pool}": pair_evidence},
                     unverified_fields=[k for k, v in fields.items() if v is None])


class GeckoAdapter(GeckoTerminalCollector):
    def __init__(self, source, limit, **options):
        super().__init__(**options)
        self.source, self.limit = source, limit

    async def _payload(self, **kwargs):
        endpoint = URL if self.source == SOURCES[0] else URL.replace("new_pools", "trending_pools")
        self.sample = await super()._payload(url=endpoint, params={"include": "base_token,quote_token"}, **kwargs)
        return self.sample

    async def fetch(self):
        rows = await super().fetch()
        included = {x["id"]: x.get("attributes", {}) for x in self.sample.get("included", []) if x.get("type") == "token"}
        raw = {x["attributes"]["address"]: x for x in self.sample["data"]}
        result = []
        for p in rows[:self.limit]:
            relationships = raw[p.pool_address]["relationships"]
            symbols = []
            for side in ("base_token", "quote_token"):
                token_id = relationships.get(side, {}).get("data", {}).get("id")
                symbol = included.get(token_id, {}).get("symbol")
                if isinstance(symbol, str) and symbol:
                    symbols.append(symbol)
            result.append(Candidate(**p.model_dump(), sources=[self.source], pair_symbols=symbols))
        return result


class DexAdapter(BaseCollector):
    def __init__(self, source, limit, lookup_limit, **options):
        super().__init__(**options)
        self.source, self.limit, self.lookup_limit = source, limit, lookup_limit
        self.issues = []

    async def fetch(self):
        feed = "token-profiles" if self.source == SOURCES[2] else "token-boosts"
        feed_url = f"{DEX}/{feed}/latest/v1"
        rows = await self.get_json(feed_url)
        if not isinstance(rows, list):
            raise CollectorError("DexScreener feed expected array")
        mints, metadata = [], {}
        observed = datetime.now(timezone.utc).isoformat()
        for row in rows:
            if not isinstance(row, dict):
                raise CollectorError("DexScreener feed expected objects")
            mint = row.get("tokenAddress")
            if row.get("chainId") == "solana" and isinstance(mint, str) and mint and mint not in mints:
                mints.append(mint)
                links = row.get("links")
                metadata[mint] = dict(source_url=feed_url, observed_at=observed,
                                      description=row.get("description"), links=links,
                                      socials_present=any(x.get("type") in ("twitter", "telegram", "discord") for x in links if isinstance(x, dict)) if isinstance(links, list) else None,
                                      boost_feed_present=self.source == SOURCES[3],
                                      amount=row.get("amount"), total_amount=row.get("totalAmount"),
                                      updated_at=row.get("updatedAt"), profile_age_seconds=None,
                                      profile_age_reason="No verified profile creation timestamp; pool/update age is not profile age.")
        result = []
        for mint in mints[:self.lookup_limit]:
            try:
                pairs = await self.get_json(f"{DEX}/token-pairs/v1/solana/{mint}")
                if not isinstance(pairs, list):
                    raise CollectorError("DexScreener token-pairs expected array")
                for row in pairs:
                    try:
                        p = normalize_pair(row, self.source)
                        # Quote-side tokens must not be relabeled as base tokens.
                        if p is not None and p.mint_address == mint:
                            p.intelligence_metadata[self.source] = metadata[mint]
                            result.append(p)
                    except (ValueError, TypeError, OverflowError) as exc:
                        self.issues.append(str(exc))
            except Exception as exc:
                self.issues.append(f"Token lookup failed: {type(exc).__name__}: {exc}")
        if mints and not result and self.issues:
            raise CollectorError("DexScreener no usable candidates: " + "; ".join(self.issues))
        return sorted(result, key=signal_key)[:self.limit]


def signal_key(p):
    return (-(p.volume_24h_usd or 0), -(p.liquidity_usd or 0), p.pool_address, p.mint_address)


def merge_candidates(rows, priority, cap):
    """Keep canonical pool metrics intact; mint aliases merge provenance, not metrics."""
    rank = {s: i for i, s in enumerate(priority)}
    ordered = sorted(rows, key=lambda p: (min(rank[s] for s in p.sources), *signal_key(p)))
    groups, by_pool, by_mint = [], {}, {}
    duplicates = Counter()
    for p in ordered:
        existing = by_pool.get(p.pool_address)
        if existing is not None and existing.mint_address != p.mint_address:
            raise CollectorError("Conflicting base mint for the same pool; refusing ambiguous data")
        existing = existing or by_mint.get(p.mint_address)
        if existing is None:
            existing = p.model_copy(deep=True)
            groups.append(existing)
        else:
            duplicates.update(p.sources)
            for key, evidence in p.intelligence_metadata.items():
                existing.intelligence_metadata.setdefault(key, evidence)
            for s in p.sources:
                if s not in existing.sources:
                    existing.sources.append(s)
            if p.pool_address != existing.pool_address and p.pool_address not in existing.alternate_pool_addresses:
                existing.alternate_pool_addresses.append(p.pool_address)
        by_pool[p.pool_address] = existing
        by_mint[p.mint_address] = existing
    return groups[:cap], len(groups), duplicates


async def discover(*, options, config=None, stats=None):
    cfg = config or DiscoverySettings()
    stats = stats if stats is not None else {}
    rows, succeeded = [], 0
    priority = cfg.source_priority.split(",")
    for source in priority:
        enabled = cfg.enable_geckoterminal if source.startswith("gecko") else cfg.enable_dexscreener
        outcome = dict(status="skipped", returned=0, deduplicated=0, kept_after_cap=0, errors=[])
        stats[source] = outcome
        if not enabled:
            outcome["skip_reason"] = "disabled by configuration"
            continue
        limit = getattr(cfg, source + "_limit")
        cls = GeckoAdapter if source.startswith("gecko") else DexAdapter
        extra = {} if cls is GeckoAdapter else {"lookup_limit": cfg.token_lookup_limit}
        try:
            async with cls(source, limit, **extra, **options) as adapter:
                found = await adapter.fetch()
                outcome["errors"] = getattr(adapter, "issues", [])
            succeeded += 1
            rows.extend(found)
            outcome.update(status="degraded" if outcome["errors"] else "success", returned=len(found))
        except Exception as exc:
            outcome.update(status="failed", errors=[f"{type(exc).__name__}: {exc}"])
            LOG.exception("DISCOVERY_SOURCE_FAILED %s", source)
        LOG.info("DISCOVERY_SOURCE %s %s", source, outcome)
    if not succeeded:
        raise CollectorError("All enabled discovery sources failed (or all disabled); no fabricated candidates")
    kept, unique, duplicates = merge_candidates(rows, priority, cfg.max_candidates)
    for source in priority:
        stats[source]["deduplicated"] = duplicates[source]
        stats[source]["kept_after_cap"] = sum(source in p.sources for p in kept)
    stats["totals"] = dict(returned=len(rows), unique=unique, duplicates=len(rows)-unique,
                           kept=len(kept), removed_by_cap=max(0, unique-len(kept)), cap=cfg.max_candidates)
    LOG.info("DISCOVERY_SUMMARY %s", stats)
    return kept


def rejection_breakdown(decisions):
    """Count structured evaluator failures, not substrings from evidence narratives."""
    reasons, combinations = Counter(), Counter()
    only_age = only_security_missing = 0
    missing_security_data = failed_security_check = 0
    security_fields = {"score", "lp_locked_pct", "mint_authority_revoked", "freeze_authority_revoked", "top_holder_concentration_pct"}
    for d in decisions:
        failures = d.get("failure_reasons", [])
        missing = set(d.get("missing_fields", []))
        if d["decision"] not in ("REJECT", "WATCH"):
            continue
        missing_security_data += bool(missing & security_fields)
        failed_security_check += any(f.startswith(("RugCheck score", "LP locked below", "Top-holder concentration", "Security disagreement:")) or "active authority" in f for f in failures)
        categories = set()
        for failure in failures:
            if failure.startswith("Pool age outside"):
                categories.add("pool_age")
            elif failure.startswith("Missing/unverified data:"):
                categories.update("missing:" + field for field in missing)
            elif failure.startswith("RugCheck score"):
                categories.add("rugcheck_score")
            elif failure.startswith("LP locked below"):
                categories.add("lp_locked")
            elif "active authority" in failure:
                categories.add("active_authority")
            elif failure.startswith("Top-holder concentration"):
                categories.add("top_holder_concentration")
            elif failure.startswith("Security disagreement:"):
                categories.add("security_disagreement")
            elif failure.startswith("liquidity_usd below"):
                categories.add("liquidity")
            elif failure.startswith("volume_24h_usd below"):
                categories.add("volume_24h")
            elif failure.startswith(("buys/sells ratio", "Undefined buys/sells")):
                categories.add("buys_sells_ratio")
            else:
                categories.add(failure)
        if not categories:
            categories.add("unclassified_reject_or_watch")
        reasons.update(categories)
        combinations[" + ".join(sorted(categories))] += 1
        only_age += categories == {"pool_age"}
        only_security_missing += bool(missing) and missing <= security_fields and all(
            f.startswith("Missing/unverified data:") for f in failures) and bool(failures)
    return dict(missing_security_data=missing_security_data, failed_security_check=failed_security_check,
                by_reason=dict(sorted(reasons.items())), combinations=dict(sorted(combinations.items())),
                only_pool_age=only_age, only_missing_rugcheck_authority=only_security_missing,
                note="Multi-label counts; ONLY means no other evaluated failure. Hard-filter failures skip volume-profile evaluation, so ONLY is not proof of buy eligibility.")
