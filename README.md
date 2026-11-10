# Autonomous Crypto Trader

> **PAPER TRADING ONLY.** Research and simulation only. No real-money execution, exchange credentials, wallet keys or signing. Users must place any real trades manually outside this software. Simulated results are not investment advice.

## Architecture
Collectors -> validated domain models -> risk limits -> paper executor -> orchestrator reports. The cycle collects CoinGecko prices, new Solana pools and RugCheck assessments; it evaluates every candidate and only opens simulated positions after all evidence and risk gates pass. Unknown security data stays unknown. Network and parsing failures raise errors and are written into a failed cycle report, never replaced with invented values.

## Setup (Python 3.11+)
```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
ruff check .
pytest
python -m act.orchestrator.cycle
```
No API keys are required by Milestone 1. Set SUPABASE_URL and SUPABASE_SERVICE_KEY together in Render environment variables for durable persistence; both may be empty for local mode. Optional environment overrides: PAPER_ONLY (must be true), LEDGER_PATH, REPORT_DIR, HTTP_TIMEOUT, HTTP_RETRIES, FEE_PCT, SLIPPAGE_PCT. Never commit .env or secrets. Tests use mocked HTTP transports and temporary ledgers, not the network.


## Cycle-observed position management (paper only)

The cycle now collects data, refreshes the durable ledger, refreshes quotes for every open holding, appends marks, processes exits, then evaluates new candidates. Exits free position slots before strategy checks the cap. This supersedes the earlier milestone note about no automatic exits; this is automatic **cycle-observed**, not continuous monitoring.

PositionSettings (pydantic) reads positive finite environment overrides:

| Name | Default | Meaning |
|---|---:|---|
| POSITION_MAX_HOLDING_HOURS | 48 | Exit at or after this age, only with a fresh quote |
| POSITION_MAX_QUOTE_AGE_SECONDS | 300 | Maximum CoinGecko provider timestamp age; future timestamps rejected |

Each holding uses its recorded Solana pool on GeckoTerminal and validates pool/token identity. Listed contracts fall back to CoinGecko token-price with last_updated_at. Old positions without pool identity try CoinGecko only. GeckoTerminal supplies no price-update timestamp here: observed_at is request observation time, provider_updated_at stays null; no claim of independent provider freshness is made. Invalid, missing, non-finite or failed quotes are unavailable, never yesterday's price or zero. Every cycle appends a position_quotes event with stale_quoted, null current_price/P&L and error details when unavailable. Stale positions remain visible and skip all exits, including max_age, until quotes recover. Missing holding marks make portfolio equity unavailable and block new entries, not overwrite historical marks.

Long exits: quote <= stop_loss gives stop_loss; quote >= take_profit gives take_profit; otherwise holding age >= configured hours gives max_age. Priority is stop, target, age. Existing PaperExecutor closes with its paper-only guard and identical ledger fees/slippage. Closing risk-reducing positions is not blocked by entry risk limits. Each closed trade preserves observed exit_price_basis, slipped exit_price, entry/exit timestamps, exit_reason and net realized_pnl including both fees and slippage. Losses remain in append-only history.

**No fake intracycle fills:** cycles start at startup and approximately 20 minutes after the previous cycle finishes while awake. A stop/target is executed at the first cycle observing it, at that cycle's observed quote with simulated slippage, NOT at the stop/target level or an invented crossing time. Prices can gap past stops. Render free-service sleep pauses monitoring entirely. No always-on or guaranteed execution claim is made.

Net unrealized P&L = (observed quote * (1 - slippage) * (1 - fee) - slipped entry) * units - entry fee. Equity = initial paper cash + net realized P&L + net unrealized P&L, requiring all holding quotes. FEE_PCT=0.25 and SLIPPAGE_PCT=0.5 remain per-side defaults. Quotes/marks are appended through the existing ledger; JSONB records and atomic persist_paper_cycle RPC already accommodate these fields. No 004 schema migration is required; RLS/private privileges and history-prefix guard remain intact.

Dashboard positions show quote, net unrealized P&L, stop/target distances (percentage of current quote), age, stale flags and provenance. Trade history includes observed/slipped exit price, reason, timestamps and net P&L. /status includes position_management observations/exits and the ordered steps. POSITION_MANAGEMENT, POSITION_STALE, POSITION_QUOTE_FAILED and PAPER_EXIT log lines provide audit evidence. No positions means no live exit demonstration; offline tests establish logic using mocked HTTP and temporary ledgers.

The local ledger is an append-only event history in JSON, rewritten atomically without deleting events. Back it up; it is single-process storage, not a concurrent database. Size means token units; prices and account values are USD. Entry and exit fees are charged on slipped notional. Risk checks include projected stop slippage and round-trip fees, aggregate open stop risk, daily loss and peak-equity drawdown. Only fully funded long positions with a stop below entry are permitted. Cash and equity include simulated fees. A stop is an invalidation assumption, not a guaranteed fill.

GeckoTerminal discovery page: https://www.geckoterminal.com/explore/new-crypto-pools/solana . The older /solana/new_pools web route is not used. The API /api/v2/networks/solana/new_pools is verified working; HTTP 429 honors Retry-After with minute-scale backoff; request/schema failures are reported honestly. No empty-success fallback. RugCheck unknown fields are explicitly listed in risks and never interpreted as safe. Collector scoring is provider-reported and not a promise of safety.

## Roadmap
- [x] Milestone 1: Python scaffold, collectors, domain models, risk checks, persistent paper ledger, orchestration, offline tests and CI.
- [ ] Milestone 2: Supabase persistence and migrations.
- [ ] Milestone 3: Data quality and historical ingestion.
- [ ] Milestone 4: Strategy research and features.
- [ ] Milestone 5: Backtesting and transaction-cost calibration.
- [ ] Milestone 6: Walk-forward evaluation.
- [ ] Milestone 7: Portfolio simulations.
- [ ] Milestone 8: Monitoring and research dashboard.
- [ ] Milestone 9: Robustness and operational hardening.
- [ ] Milestone 10: Reproducible paper-trading evaluation. Real-money execution remains out of scope.

## Deployment
Deploy `render.yaml` in Render (My Workspace) as one **Free** Python web service. Never select a paid instance or add a database/disk. Build: `pip install -r requirements.txt`. Start: `uvicorn act.api:app --host 0.0.0.0 --port $PORT --workers 1`. No secret environment variables, exchange credentials, or wallet keys are needed.

The `/` dashboard shows the latest real cycle: BTC/ETH/SOL USD prices, up to ten newly discovered Solana pools, security assessments, paper equity, and explicit collector success/failure/skipped outcomes. `/health` reports uptime, paper-only mode and the last cycle timestamp. `/status` exposes the full latest cycle JSON. A cycle starts at startup, then repeats every 20 minutes after completion while awake; results are appended to `reports/cycles.jsonl` and printed as `CYCLE_RESULT` / `COLLECTOR` log lines. The orchestrator stops at its first collector error: downstream collectors are honestly marked skipped, not successful. GeckoTerminal failures remain explicit; screened candidates can open PAPER trades only.

**Free-tier caveat:** Render may spin down idle free services, so this is not an always-on scheduler. Sleep pauses research, and waking starts a new cycle. Memory, local JSONL reports and the simulated ledger are ephemeral and may be lost on restart/redeploy; initial paper equity comes from the ledger, not live funds. No paid persistence is configured. Save important reports externally before a restart. Missing or failed data is never fabricated or interpreted as safe.


## Evidence-gated strategy (paper only)

Every discovered pool produces a BUY / REJECT / WATCH DecisionRecord, with mint, symbol, provider score (HIGHER IS WORSE), explicit evidence and missing-data reasons. BUY means the paper executor actually opened a simulated position; executor or risk rejection changes it to REJECT. No wallet, keys, exchange API or real-money execution exists. An all-rejected cycle is successful and must never trigger looser filters.

Pydantic `StrategySettings` reads these environment overrides (defaults):

| Name | Default | Rule |
|---|---:|---|
| STRATEGY_MAX_RUGCHECK_SCORE | 500 | score strictly below |
| STRATEGY_MIN_LP_LOCKED_PCT | 80 | LP locked at least |
| STRATEGY_MIN_LIQUIDITY_USD | 10000 | at least USD |
| STRATEGY_MIN_VOLUME_24H_USD | 25000 | at least USD |
| STRATEGY_MIN_BUYS_SELLS_RATIO | 1.1 | at least; zero sells is undefined and rejected |
| STRATEGY_MAX_POOL_AGE_HOURS | 72 | 0 through 72 inclusive |
| STRATEGY_STOP_FRACTION | 0.15 | stop below quoted entry |
| STRATEGY_TARGET_FRACTION | 0.40 | target above quoted entry |
| STRATEGY_MAX_OPEN_POSITIONS | 3 | cap; settings cannot exceed 3 |

Missing score, LP lock, authority revocation or holder concentration data rejects, even if score is 1 and LP is 100%. Active mint/freeze authorities reject too. Missing prices, transaction counts, liquidity or volume never pass. Pool age uses GeckoTerminal pool_created_at, stored as first_seen_at. The decision data timestamp is evaluation time for this cycle, not a claim about provider freshness.

Nominal notional = (current equity * 1%) / stop fraction; units = notional / quoted entry. Actual size is conservatively reduced so slipped entry + entry fee minus slipped stop proceeds after exit fee stays within the existing **1% inclusive-cost ceiling**. Both nominal and actual size are recorded. FEE_PCT=0.25 and SLIPPAGE_PCT=0.5 are per-side defaults; estimates are round-trip at stop. Max estimated loss is not a guaranteed loss ceiling on gaps. Daily loss 3%, peak drawdown 10%, available cash and aggregate open-stop exposure still gate every executor open; no override weakens them. Missing current quotes for previously opened mints blocks new opens (WATCH) and reports unavailable equity rather than inventing marks. This milestone does not add a continuous price-monitoring/automatic exit service. Existing executor close retains losing trades.

Apply migrations 001, 002, then `supabase/migrations/003_decisions.sql` in the Supabase SQL editor. 003 adds private RLS-enabled paper_decisions with no public policies; anon/authenticated privileges are revoked. The existing single `persist_paper_cycle` RPC atomically persists cycle, decisions and ledger. Existing ledger history must remain a prefix of each new history; paper_trades uses conflict DO NOTHING, never overwriting losses. Only backend service role may call it. Never put secrets in SQL, source or chat.

The dashboard and /status show all latest-cycle decisions, proposals, equity, open paper positions and all closed paper trades. PAPER_DECISION, PERSISTED_CYCLE and CYCLE_RESULT log lines provide audit evidence. Offline tests in tests/test_strategy.py use fixtures/temp ledgers, never network.

## Volume profile confirmation (PAPER only)

`act.volume_profile` computes a **candle-derived approximation**, not trade-by-trade volume-at-price or order flow. Equal-width price bins span the observed low/high. Each candle's USD volume is distributed uniformly over its high-low range by overlap with each bin. A flat candle contributes entirely to its containing bin (the top endpoint is clamped to the last bin). An all-flat history has a single price level. Nonfinite, inconsistent, duplicate-timestamp, empty or zero-total-volume data fails loudly.

POC is the highest-volume bin's midpoint (ties choose the lower bin). Starting at POC, the contiguous value area expands one adjacent bin at a time toward the larger adjacent volume, ties downward, until at least the configured percentage is included. VAL/VAH are the outer edges; actual enclosed percentage may exceed the target and is recorded.

**Entry:** all existing hard filters and risk gates remain unchanged. OHLCV is fetched only after every hard strategy filter passes and equity is available. BUY then requires current observed price **at or above VAH**, with VAL strictly below entry. Inside value area is rejected pending acceptance; below VAL is invalidation and rejected. No profile, exhausted retries, malformed/stale candles or insufficient history means REJECT, never BUY. Hard-filter rejects explicitly say profile not attempted. Profile evidence is retained in each eligible decision's existing proposal JSONB, including unavailable reasons; no new database columns/migration are required.

**Stop/sizing:** stop is VAL unless farther than `STRATEGY_STOP_FRACTION` from entry, in which case the fraction cap is used and explicitly labeled (stop above VAL). Nominal sizing is 1% equity / fractional stop distance, then reduced for fees/slippage so estimated stop loss remains within the existing cost-inclusive 1% gate. Targets, concurrency, loss/drawdown gates and stale-quote protections are unchanged. Stops are not guaranteed fills: existing first-observing-cycle simulation fills still apply.

### OHLCV contract and rate discipline

Verified on 2026-11-09 using the public SOL/USDC pool `DJNtGuBGEQiUCWE8F981M2C3ZghZt2XLD8f2sQdZ6rsZ`:
`https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/minute?aggregate=15&limit=100&currency=usd&token=base&include_empty_intervals=false`.
The path timeframe is **minute**, not `15m`; aggregate is 15 or 5. `data.attributes.ohlcv_list` contains newest-first `[timestamp, open, high, low, close, volume]` rows. The captured response has `data.type=ohlcv_request_response`, `meta.base` and `meta.quote` token metadata; no links were present. The exact 12-row real sample is pinned in `tests/test_volume_profile.py`. The sample succeeded without 429; no empirical rate limit threshold is claimed.

Use 15m for older pools; pools under 24h use 5m directly. Too few completed 15m candles fall back to 5m. Request at most 100 rows, discard incomplete candles, reject future timestamps or latest completed candle older than three intervals, and require 12 completed candles by default. A per-cycle fetcher caches successes **and failures** by pool address. Existing GeckoTerminal bounded retries/429 Retry-After-aware backoff are reused; no extra OHLCV requests for hard-filter rejects. The cycle reports unique attempted pools, available/unavailable profiles and not-attempted candidates. Young pool history is limited/coarse, may contain gaps, and cannot establish profitable acceptance. No candles or marks are fabricated.

### Volume-profile environment settings

Pydantic `VolumeProfileSettings` reads these environment overrides (no secrets):

| Setting | Default | Bounds / policy |
|---|---|---|
| `VOLUME_PROFILE_TIMEFRAME` | `15m` | `15m` or `5m` |
| `VOLUME_PROFILE_FALLBACK_TIMEFRAME` | `5m` | `5m` |
| `VOLUME_PROFILE_VALUE_AREA_PCT` | `70` | >0, <=100 |
| `VOLUME_PROFILE_MIN_CANDLES` | `12` | 1..1000; API request limit is 100, so higher requirements fail closed |
| `VOLUME_PROFILE_BINS` | `24` | 1..200 |
| `VOLUME_PROFILE_REQUIRED_FOR_BUY` | `true` | Mandatory safety invariant: `false` is rejected, never a bypass |

The read-only dashboard keeps **SIMULATED - PAPER TRADING ONLY** prominent and shows per-candidate POC/VAH/VAL, target/actual VA%, price position, candle count, timeframe and unavailable/not-attempted status. Synthetic hand-computed tests are explicitly offline fixtures, not live evidence.

## Multi-source discovery (paper only)

Discovery uses independent, ordered keyless public adapters. Contracts were checked against real unauthenticated JSON on 2026-11-10:

- GeckoTerminal: `https://api.geckoterminal.com/api/v2/networks/solana/new_pools?include=base_token` and `/api/v2/networks/solana/trending_pools?include=base_token`. Pool/base-token addresses, creation time, available liquidity, volume and transaction counts retain the existing strict parser.
- DexScreener: `https://api.dexscreener.com/token-profiles/latest/v1` and `/token-boosts/latest/v1`, filtering `chainId=solana`, then `/token-pairs/v1/solana/{tokenAddress}`. Pair address, base/quote symbols, creation timestamp, price, volume, liquidity and transactions come directly from DexScreener, not a fabricated GeckoTerminal lookup. Missing liquidity remains unknown and fails the existing gate. A real checked pair sample is `/token-pairs/v1/solana/86eESqktK5qmPyZ5mHPoELR32uj9YYa4uB4iMhp6pump`. Boosted/promoted tokens receive no safety exemption.
- Not adopted: DexScreener search for `SOL` is ambiguous and includes other chains. CoinGecko `/api/v3/search/trending` and `/coins/list?include_platform=true` returned keyless JSON, but trending lacks mint addresses and requires an expensive platform mapping plus pool resolution. CoinGecko market context remains independent; there is no CoinGecko discovery adapter or enable flag.

These observations establish access and response shapes, not a permanent availability promise. No 429 occurred during contract sampling; no observed request-rate threshold is claimed. Existing bounded Retry-After-aware retries apply. Fetching is sequential: two GeckoTerminal requests and up to two Dex feeds plus five token lookups per feed by default, before retries. Missing or malformed data is never invented.

### Configuration and merge policy

Pydantic settings read environment overrides; defaults need no Render environment changes:

| Environment variable | Default |
| --- | --- |
| `DISCOVERY_MAX_CANDIDATES` | `40` (1..100) |
| `DISCOVERY_ENABLE_GECKOTERMINAL` | `true` |
| `DISCOVERY_ENABLE_DEXSCREENER` | `true` |
| `DISCOVERY_GECKOTERMINAL_NEW_LIMIT` | `15` (1..20) |
| `DISCOVERY_GECKOTERMINAL_TRENDING_LIMIT` | `10` (1..20) |
| `DISCOVERY_DEXSCREENER_PROFILES_LIMIT` | `10` (1..30) |
| `DISCOVERY_DEXSCREENER_BOOSTS_LIMIT` | `10` (1..30) |
| `DISCOVERY_TOKEN_LOOKUP_LIMIT` | `5` per Dex feed (1..10) |
| `DISCOVERY_SOURCE_PRIORITY` | `geckoterminal_new,geckoterminal_trending,dexscreener_profiles,dexscreener_boosts` |

Priority must contain every adapter exactly once. Within priority, descending available volume, descending liquidity, then addresses break ties deterministically. Deduplication uses both pool and base-token mint. The highest-priority canonical pool keeps its own metrics; sources are unioned and alternate pool addresses recorded, never splicing metrics from different pools. Conflicting mint identities for one pool fail loudly. The global cap is applied only after deduplication, bounding downstream security work at 40 candidates by default. Per-source limits bound normalized outputs; feed length is not a promise of usable Solana pools.

Each source can succeed, degrade, fail or be skipped. A failed source is logged and persisted while others continue. All enabled sources failing (or no enabled sources) raises a loud cycle failure, never synthetic candidates. CoinGecko context failure does not suppress independent discovery. Cycle JSON contains `discovery_stats` with per-source normalized returned, deduplicated, kept-after-cap and errors, plus aggregate unique/duplicate/cap counts. Sources carried by a canonical candidate can each count it as kept; these are provenance memberships, not additive unique counts. Decisions retain sources, canonical pool and alternate pools in their proposal JSON alongside structured failure reasons.

### Rejection observability and unchanged gates

The dashboard retains the prominent paper-only banner and shows candidate sources, source health/counts, and the latest cycle's `rejection_breakdown`. Multi-label reason counts and combinations distinguish pool age, security/authority missing data, liquidity, volume, transaction ratio and other gates. `only_pool_age` and `only_missing_rugcheck_authority` mean ONLY the evaluated gates: hard-screen failures skip volume-profile confirmation, so these counts do not prove the candidate would otherwise be a BUY. Missing non-security fields are separate failures.

All strategy/risk defaults remain unchanged, including RugCheck score 500 (lower is better), LP lock 80%, liquidity $10,000, 24h volume $25,000, buys/sells 1.1, maximum pool age 72 hours, and stop fraction 0.15. Mandatory VAH/VAL volume-profile confirmation and fail-closed unknown data remain mandatory. Trending pools are often older than 72 hours and will legitimately fail age screening. Existing cycle JSON/proposal persistence is used; no new table is required. Offline tests pin real response projections and use mocked transports; tests must never call live APIs.


## Onchain primary security (paper research only)

### Forensic evidence, 2026-11-10
The collector used keyless GET https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary. For three mints in cycle 5c176f7d-22db-462b-b2b4-96bf2d817b5b (finished 14:19:48 UTC), stored mint/freeze/concentration were all null. Each raw summary genuinely omitted mintAuthority, freezeAuthority and topHolders; the existing parser already distinguishes absent keys from explicit null. This is an endpoint/data-shape gap, not a lost-null parsing bug. Full GET /report returns explicit null authorities and 20 topHolders for each sample:

| Sample | Mint | Summary score | Stored LP % | Full authority values |
|---|---|---:|---:|---|
| AICoin | B9FgqHbtccsgcL6YPfUhNd81qveNtrTbsfLvcjxtpump | 1 | 94.6695026022904 | mint=null, freeze=null |
| FAIR | FairDR2UmRqHxsVPdPcetePCjFaDqVYv3oQX9fcQTpTg | 1 | 82.94435947092916 | mint=null, freeze=null |
| DACITYCOIN | 76indaHnwAuKrK2oBpBcPsVe1jRbYHCRYDsYuJ1MVys4 | 1 | 99.98315786706333 | mint=null, freeze=null |

The enabled enrichment reads /report for comparison without overriding summary score or LP. Explicit-null and nested-token parsing remain supported and regression-tested. Raw projections and original summary assessment are persisted in security_evidence and each decision proposal.

### Contracts and exact method
Use only read-only finalized Solana mainnet JSON-RPC, no wallet, signer, exchange API, key or paid service. Official contracts: https://solana.com/docs/rpc/http/getaccountinfo, https://solana.com/docs/rpc/http/gettokenlargestaccounts, https://solana.com/docs/rpc/http/gettokenaccountsbyowner.

1. getAccountInfo(mint, jsonParsed) must be a non-executable initialized SPL Token or Token2022 mint with explicit mintAuthority/freezeAuthority keys, positive integer supply and decimals. Explicit null = revoked/pass; valid non-null address = active/reject; absent/invalid = unknown/reject. Zero supply (including native wrapped SOL mint's observed zero supply) cannot be a denominator.
2. getTokenAccountsByOwner(candidate pool address, mint, jsonParsed) identifies that pool's token accounts and validates parsed owner/mint/program. **Not all AMMs use the pool address as vault authority.** Empty/unidentified result means concentration unverifiable, not zero. No guessed vault address, offchain-only vault or silent inclusion.
3. Query the same mint's token accounts owned by known incinerator 1nc1nerator11111111111111111111111111111111; exclude those accounts and the literal incinerator address. No other alleged burn addresses are assumed.
4. getTokenLargestAccounts returns at most 20 token ACCOUNT addresses, NOT beneficial owners. Validate unique addresses, raw integer balances, ordering, decimals and sum <= supply. Exclude identified candidate-pool accounts and the incinerator accounts, then sum the largest N remaining balances / unchanged total mint supply * 100. Do not subtract exclusions from supply. If 20 returned accounts leave fewer than N after exclusions, the truncated list cannot settle top N: unverifiable/reject. Fewer than 20 is treated as the complete nonzero list. N is constrained 1..20. Integer arithmetic is used before the final percentage division.
5. All read slots must be within 150 slots. These are nearby finalized snapshots, not an atomic single-slot audit. No cross-cycle evidence reuse. Token2022 extensions (transfer fees, permanent delegates etc.) are not a comprehensive security audit.

Onchain values alone are primary for the three fields. Enabled mode never substitutes RugCheck when onchain is missing. When both provide a comparable fact and disagree, effective field becomes missing AND Security disagreement explicitly rejects. Full RugCheck concentration is recomputed with the same N/exclusions/denominator, never compared with its differently-defined aggregate. A comparison that cannot be made is labeled unverifiable. Existing score, LP, market/risk/volume-profile gates remain unchanged; missing values still reject.

### Configuration and rate discipline
Set these optional non-secret Render environment variables; defaults work without new secrets:

| Name | Default | Meaning |
|---|---|---|
| SECURITY_ONCHAIN_ENABLED | true | false restores original RugCheck-only behavior; missing still rejects, never bypass |
| SECURITY_MAX_TOP_HOLDERS_PCT | 0.50 | Fraction of supply; strict concentration <50%, equality rejects |
| SECURITY_TOP_HOLDERS_N | 10 | Top eligible token accounts, 1..20 |
| SOLANA_RPC_URL | https://api.mainnet-beta.solana.com | Only verified official keyless mainnet endpoints allowed (also api.mainnet.solana.com) |
| SOLANA_RPC_CYCLE_BUDGET | 24 | HTTP attempts, including retries, hard cap per cycle (1..100) |
| SOLANA_RPC_RETRIES | 1 | Additional attempts per RPC (0..2), bounded backoff <=10s |

The 0.50 maximum is the explicitly requested conservative policy default, **not an empirical calibration from successful largest-account live responses**. No previous threshold is relaxed. Liquidity/volume/age preliminary rejection uses the same strategy thresholds before RPC spending; skipped candidates keep unknown onchain fields. One client/cache per cycle caches successes and failures. At most four distinct RPC calls per complete candidate plus bounded retries; budget exhausted, 429, transport, invalid JSON/result and missing vaults are recorded loudly per candidate. SECURITY_SOURCES, PAPER_DECISION and REJECTION_BREAKDOWN logs plus dashboard provenance expose calls/budget/errors/source counts and separate missing_security_data from failed_security_check (overlapping multi-label counts). Persistence uses existing report/proposal JSON; no schema migration.

Observed browser probes: real mint and pool-owner requests returned200 for mint 6sGK9xX2wSsMiUTTo3xZBKe7fcydF3rtQnSGtKJMpump (supply 1000000000000000, decimals6, both authorities null), and owner-query returned account 9LSQizvrMgMeUq5BuZQaNSSwdox2x8NmYvmk6pJKPWhe owned by E3rYVe57SbyhvZHZnjwh7L1Wd74vyk5M3CRFkeSj6wdQ. getTokenLargestAccounts returned429 with 'Too many requests for a specific RPC call' at both official endpoints. PublicNode returned403 'Indexed requests require a personal token', so it is not configured as a fallback. These observations are not a guaranteed universal quota. A keyless endpoint may remain unable to deliver concentration; that is a loud data gap and no BUY, never fabricated data.

Offline tests distinguish captured real response projections/error fixtures from synthetic arithmetic edge cases. They never call the network. First-observing-cycle paper fills and existing fees/slippage/risk stops remain unchanged. All-rejected cycles can be correct; a verified authority is progress, not permission to trade without the remaining verified gates.
