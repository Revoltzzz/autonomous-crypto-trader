"""Keyless, read-only Solana mainnet security evidence. Unknown is never safe."""
import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from act.collectors.base import BaseCollector, CollectorError, retry_delay

TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}
BURN_OWNER = "1nc1nerator11111111111111111111111111111111"
FIELDS = ("mint_authority_revoked", "freeze_authority_revoked", "top_holder_concentration_pct")


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", allow_inf_nan=False)
    security_onchain_enabled: bool = True
    security_max_top_holders_pct: float = Field(default=0.50, gt=0, le=1)
    security_top_holders_n: int = Field(default=10, ge=1, le=20)
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_rpc_cycle_budget: int = Field(default=24, ge=1, le=100)
    solana_rpc_retries: int = Field(default=1, ge=0, le=2)

    @field_validator("solana_rpc_url")
    @classmethod
    def keyless_mainnet(cls, value):
        # Intentionally restrict configuration to verified keyless mainnet endpoints.
        u = urlsplit(value)
        if (u.scheme != "https" or u.hostname not in
                {"api.mainnet-beta.solana.com", "api.mainnet.solana.com"}
                or u.username or u.password or u.query or u.fragment or u.path not in ("", "/")):
            raise ValueError("Only verified keyless public mainnet RPC URLs are allowed")
        return value


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", value):
        raise CollectorError("Invalid base58 address")
    return value


def units(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise CollectorError("Raw token amount must be an unsigned integer string")
    return int(value)


def parsed_account(value, kind):
    if not isinstance(value, dict) or value.get("owner") not in TOKEN_PROGRAMS or value.get("executable") is not False:
        raise CollectorError("Unsupported token program or invalid account")
    parsed = value.get("data", {}).get("parsed", {})
    if parsed.get("type") != kind or not isinstance(parsed.get("info"), dict):
        raise CollectorError("Expected jsonParsed token " + kind)
    return parsed["info"]


def mint_evidence(result):
    info = parsed_account(result.get("value"), "mint")
    if info.get("isInitialized") is not True:
        raise CollectorError("Mint is not initialized")
    for key in ("mintAuthority", "freezeAuthority"):
        if key not in info:
            raise CollectorError("Missing explicit " + key)
        if info[key] is not None:
            address(info[key])
    supply = units(info.get("supply"))
    if supply <= 0 or type(info.get("decimals")) is not int:
        raise CollectorError("Positive supply and integer decimals required")
    return {"mintAuthority": info["mintAuthority"], "freezeAuthority": info["freezeAuthority"],
            "supply": str(supply), "decimals": info["decimals"],
            "mint_authority_revoked": info["mintAuthority"] is None,
            "freeze_authority_revoked": info["freezeAuthority"] is None,
            "slot": result["context"]["slot"]}


def owned_accounts(result, owner, mint):
    rows = result.get("value")
    if not isinstance(rows, list):
        raise CollectorError("Missing token accounts array")
    found = set()
    for row in rows:
        info = parsed_account(row.get("account"), "account")
        if info.get("owner") != owner or info.get("mint") != mint:
            raise CollectorError("Token account owner/mint identity mismatch")
        found.add(address(row.get("pubkey")))
    return found


def concentration(result, supply, decimals, pool_accounts, burn_accounts, n):
    if not pool_accounts:
        raise CollectorError("Candidate pool token accounts unidentified")
    rows = result.get("value")
    if not isinstance(rows, list) or not rows or len(rows) > 20:
        raise CollectorError("Missing/invalid largest token accounts")
    balances, seen = [], set()
    excluded = set(pool_accounts) | set(burn_accounts) | {BURN_OWNER}
    total_listed = 0
    last = None
    for row in rows:
        key = address(row.get("address"))
        amount = units(row.get("amount"))
        if key in seen or row.get("decimals") != decimals or (last is not None and amount > last):
            raise CollectorError("Invalid/unsorted/duplicate largest accounts")
        seen.add(key)
        total_listed += amount
        last = amount
        if key not in excluded:
            balances.append(amount)
    if total_listed > int(supply):
        raise CollectorError("Listed balances exceed mint supply; inconsistent snapshot")
    if len(rows) == 20 and len(balances) < n:
        raise CollectorError("Top-20 truncation cannot establish requested eligible top N")
    numerator = sum(balances[:n])
    return {"top_holder_concentration_pct": numerator * 100 / int(supply),
            "numerator_raw": str(numerator), "denominator_raw": supply,
            "n": n, "eligible_accounts_summed": min(n, len(balances)),
            "excluded_pool_accounts": sorted(pool_accounts),
            "excluded_burn_accounts": sorted(burn_accounts | {BURN_OWNER}),
            "largest_accounts": rows, "slot": result["context"]["slot"]}


class SolanaRPCCollector(BaseCollector):
    def __init__(self, *, config=None, client=None, sleep=asyncio.sleep, timeout=15):
        super().__init__(client=client, sleep=sleep, timeout=timeout, retries=0)
        self.config = config or SecuritySettings()
        self.calls = 0
        self.cache = {}
        self.events = []

    async def rpc(self, method, params):
        if method not in {"getAccountInfo", "getTokenAccountsByOwner", "getTokenLargestAccounts"}:
            raise CollectorError("Non-read-only RPC method forbidden")
        key = (method, repr(params))
        if key in self.cache:
            cached = self.cache[key]
            if isinstance(cached, Exception):
                raise CollectorError(str(cached))
            return cached
        try:
            for attempt in range(self.config.solana_rpc_retries + 1):
                if self.calls >= self.config.solana_rpc_cycle_budget:
                    raise CollectorError("Solana RPC per-cycle budget exhausted")
                self.calls += 1
                event = {"method": method, "attempt": attempt + 1, "call": self.calls}
                self.events.append(event)
                try:
                    r = await self.client.post(self.config.solana_rpc_url,
                        json={"jsonrpc": "2.0", "id": self.calls, "method": method, "params": params},
                        timeout=self.timeout)
                    event["http_status"] = r.status_code
                    if r.status_code == 429 or r.status_code >= 500:
                        if attempt < self.config.solana_rpc_retries:
                            await self.sleep(min(10, max(2 ** (attempt + 1), retry_delay(r.headers.get("Retry-After"), 0))))
                            continue
                        raise CollectorError(f"Solana RPC {method} HTTP {r.status_code}; retries exhausted")
                    r.raise_for_status()
                    data = r.json()
                    if data.get("error") or "result" not in data:
                        raise CollectorError(f"Solana RPC {method} error: {data.get('error')}")
                    self.cache[key] = data["result"]
                    return data["result"]
                except httpx.TransportError as exc:
                    event["error"] = type(exc).__name__
                    if attempt == self.config.solana_rpc_retries:
                        raise CollectorError("Solana RPC transport failure") from exc
                    await self.sleep(2 ** (attempt + 1))
            raise CollectorError("Solana RPC exhausted")
        except Exception as exc:
            error = CollectorError(str(exc))
            self.cache[key] = error
            raise error from exc

    async def fetch(self, mint, pool):
        address(mint)
        address(pool)
        evidence = {"source": "onchain", "endpoint": self.config.solana_rpc_url,
                    "observed_at": datetime.now(timezone.utc).isoformat(), "errors": []}
        opts = {"encoding": "jsonParsed", "commitment": "finalized"}
        try:
            evidence.update(mint_evidence(await self.rpc("getAccountInfo", [mint, opts])))
        except Exception as exc:
            evidence["errors"].append("mint: " + str(exc))
            return evidence
        try:
            pool_result = await self.rpc("getTokenAccountsByOwner", [pool, {"mint": mint}, opts])
            pool_accounts = owned_accounts(pool_result, pool, mint)
            if not pool_accounts:
                raise CollectorError("Candidate pool token accounts unidentified; no decoded vault support")
            burn_result = await self.rpc("getTokenAccountsByOwner", [BURN_OWNER, {"mint": mint}, opts])
            burn_accounts = owned_accounts(burn_result, BURN_OWNER, mint)
            largest = await self.rpc("getTokenLargestAccounts", [mint, {"commitment": "finalized"}])
            slots = [evidence["slot"], pool_result["context"]["slot"],
                     burn_result["context"]["slot"], largest["context"]["slot"]]
            if max(slots) - min(slots) > 150:
                raise CollectorError("RPC response slots differ by more than 150; inconsistent observation")
            evidence.update(concentration(largest, evidence["supply"], evidence["decimals"],
                pool_accounts, burn_accounts, self.config.security_top_holders_n))
        except Exception as exc:
            evidence["errors"].append("concentration: " + str(exc))
        return evidence
