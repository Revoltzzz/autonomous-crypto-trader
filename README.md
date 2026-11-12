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

## Evidence-first intelligence (ta-v1; gates deliberately OFF)

This layer measures and displays evidence **after** the unchanged strategy has evaluated/executed its paper decision. It does not reject, score, resize or approve a trade. Existing security, market, volume-profile, risk and executor semantics are unchanged. Zero BUYs is an honest possible outcome, not a reason to loosen gates.

Configuration (defaults): `INTELLIGENCE_TA_ENABLED=true` computes/records TA; false records disabled/unavailable indicators. `INTELLIGENCE_GATES_ENABLED` defaults false and only false is permitted in this release: there is intentionally no threshold policy to enable. Any future gating requires a reviewed policy/code change, explicit owner-selected thresholds and regression tests. No sentiment collector is enabled and its per-cycle external request budget is **zero**. No paid services, exchange APIs, wallet keys, signup or new dependency.

### Data inventory and reuse

- The existing volume-profile fetcher requests GeckoTerminal `/api/v2/networks/solana/pools/{pool}/ohlcv/minute`, USD/base, limit100, aggregate15 (15m) or5 (5m for young pools/fallback), `include_empty_intervals=false`. Only validated completed candles are used. Its existing min12, freshness checks, retries, fallback and per-cycle cache are unchanged.
- Crucially, OHLCV is fetched **only after existing hard-filter preflight passes**. TA does not fetch missing candles. Rejects without an existing fetch get unavailable indicators, not invented neutral scores. A cycle can legitimately show TA0/N. This sampling bias must be considered in later threshold research.
- Previously candle rows were discarded after volume-profile computation; successful profiles now retain those same rows (at most100), exact request params/source URL, fetch/evaluation times and oldest/newest timestamps inside existing cycle JSON. There are no duplicate requests. These are replay inputs, not an executable replay engine or a promise of historical L2 data. Local retention is ephemeral; configured Supabase stores the existing report/proposal JSON without migration.
- Gecko discovery already retains price, liquidity, 24h volume/transactions and pool creation; additional raw price-change windows were not retained and are not fabricated. DexScreener's already-fetched profile/boost feeds and token-pair responses now retain description, social links present/absent/unknown, boost feed membership/amounts and observation/source provenance. A boost is promotion metadata, not positive sentiment; an omitted boost field is unknown. No verified profile-creation timestamp exists in captured responses: profile age is unavailable, never inferred from pool creation or `updatedAt`.
- RugCheck summary provides score/risks/security fields; the existing security layer separately requests full reports for eligible candidates and reconciles authorities and topHolders concentration with on-chain evidence. Intelligence does not change its normalization, thresholds or requests. CoinGecko observations remain **market-wide price context, not token sentiment**.

### Indicator definitions

Chronological clean contiguous bars only; gaps, invalid/duplicate/nonfinite rows or absent history make the window unavailable with a reason. No interpolation/resampling/zero-filling. Every indicator stores raw value, availability, reason, minimum bars and observed window size; TA includes schema/computation version, timeframe/bar count and source timestamps. Fractions below are ratios, not percentages.

| Indicator | Definition / minimum bars |
| --- | --- |
| RSI14 | Wilder-smoothed gains/losses, first14-change SMA seed; min15. All-flat RSI undefined, rising-only100, falling-only0. |
| EMA9 / EMA21 | SMA-seeded exponential averages, alpha2/(n+1); min9/21. |
| EMA slopes | Final EMA / preceding EMA -1 (one bar); min10/22. |
| EMA relationship | EMA9 / EMA21 -1; min21. |
| Momentum5 | Last close / close5 intervals earlier -1; min6. |
| ATR14 normalized | Wilder average of true ranges against previous close (first14 ranges seed) / last close; min15. |
| Volume ratio5 | Sum last5 volumes / sum preceding5; min10, zero denominator unavailable. |
| Drawdown | Last close / maximum high of retained window -1; min2. |
| Bars since POC touch | Completed bars after most recent low<=full-window POC<=high; no touch unavailable. This is descriptive, uses full-window POC and is not a causal historical entry signal. |

`proposal.intelligence` is added to **every evaluated candidate**, preserving existing proposal fields. Cycle `intelligence_stats` counts availability per indicator and reasons; `ta_computed` means at least one indicator available, not all. `elapsed_ms` measures this CPU attachment only, not collection or persistence latency. The dashboard adds an escaped expandable intelligence section; old records explicitly say not recorded. `INTELLIGENCE_STATS` and existing `PAPER_DECISION` logs retain evidence. Synthetic hand-computed tests and mocked persistence/HTTP tests run offline.

### Free/keyless source verification — 2026-11-11

These are captured browser results, not guarantees of availability from every hosting network. No API-key signup or authentication was used.

| Source / probe | Actual result and adoption decision |
| --- | --- |
| [Reddit CryptoCurrency JSON](https://www.reddit.com/r/CryptoCurrency/new.json?limit=3), [Solana JSON](https://www.reddit.com/r/solana/new.json?limit=3), [mint search](https://www.reddit.com/search.json?q=HAhZGYQzGHXkbfukyvs3Vj6t7nfqgWUym1LCKRZcpump&limit=3) | Network-security block, no usable JSON; old.reddit Solana route redirected to login. Not adopted; rate budget cannot be verified. |
| [CoinDesk RSS](https://www.coindesk.com/arc/outboundfeeds/rss/) | Real keyless RSS XML, build2026-11-11 08:43:16UTC. Market news, not evidence of new Solana token coverage. Verified but not adopted as runtime sentiment. |
| [The Block RSS](https://www.theblock.co/rss.xml) | Real keyless RSS XML, build2026-11-11 07:25:17UTC; Bitmine/ether-loss story. Same scope limit; not runtime sentiment. |
| [Decrypt RSS](https://decrypt.co/feed) and `?output=rss` | Cloudflare security-verification page, no feed captured; not adopted. |
| [Dex profiles](https://api.dexscreener.com/token-profiles/latest/v1), [boosts](https://api.dexscreener.com/token-boosts/latest/v1), [recent RINGPAD mint pairs](https://api.dexscreener.com/token-pairs/v1/solana/HAhZGYQzGHXkbfukyvs3Vj6t7nfqgWUym1LCKRZcpump) | Real keyless JSON. RINGPAD profile had description, website and Twitter link; pair response had socials. Adopt only already-collected metadata, **not mentions/sentiment**. No verified profile creation time. |

No genuinely free keyless Solana-mint-to-social-mentions source was verified. Mainstream headlines cannot honestly substitute for brand-new micro-cap coverage. X/Twitter APIs are not used. This release skips new news/sentiment ingestion rather than pretend token coverage or score headlines; sentiment is explicitly unavailable. If a source is adopted later it must first pass real-response/schema/identity verification and gain a bounded budget, retries, cycle cache, source isolation and loud error reporting.

### HFTENGINE reference review (Phase0c)

Read-only review of [mirkovicdev/HFTENGINE](https://github.com/mirkovicdev/HFTENGINE), README, structure, `tools/session.py`, `runner/src/record.rs`, `runner/src/queue.rs` and [LICENSE](https://github.com/mirkovicdev/HFTENGINE/blob/main/LICENSE). It is a Rust/TypeScript hftbacktest replay console for recorded Binance USDT-M futures **backtests, not a live trading bot**. Root license: **MIT, Copyright(c)2026 Antonije Mirkovic**. No code/assets were copied and that repository was not modified. Its hftbacktest submodule and fonts have separate licenses; no assets/dependencies were imported.

- **Adopted now, reimplemented in Python:** retain original observed OHLCV alongside decision snapshots with source/time/params/version, inspired by feed recording/replay sessions; expose state and missing evidence without changing strategy policy. This is groundwork for a future learning/replay engine, not that engine itself.
- **Roadmap Phase4 (Binance paper research):** evaluate hftbacktest's recorded-L2 replay, queue-position scenarios, latency and fee sensitivity. Still subject to paper-only scope and a separate reviewed design; no Rust service introduced on the free tier.
- **Modeling honesty now:** continue identifying existing paper fees/slippage as modeled, not measured execution; OHLCV cannot establish orderbook queue, market impact, partial fills or real network latency. HFTENGINE's tutorial rebate/retail fee and laptop/tutorial latency comparisons are scenario examples, not assumptions transferable to Solana. No new fill/fee model or numerical assumptions were adopted.
- **Inapplicable:** futures market-making grid/L2 queue policies, Binance live connectors, Rust deployment, Windows recording scripts, UI/fonts/assets. Different chain/market/data granularity/stack; importing them would not create honest micro-cap evidence and could violate scope.

### Choosing thresholds later

First accumulate versioned, timestamped availability and indicator distributions across many independent cycles, pools, ages and regimes. Track missingness and preflight selection bias, costs, later observed outcomes and duplicated candidates; avoid look-ahead (including full-window POC), leakage and survivorship bias. Then the owner deliberately selects thresholds using chronological holdout/offline paper replay and documented cost/latency assumptions. Until that reviewed change, all intelligence remains evidence-only. No promise of more trades or profitability.


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
| STRATEGY_STOP_FRACTION | 0.15 | fraction floor below slipped entry; tighter VAL preserved |
| RISK_MAX_POSITION_NOTIONAL_FRACTION | 0.02 | maximum slipped-entry notional / current equity; finite (0, 1] |
| STRATEGY_TARGET_FRACTION | 0.40 | target above quoted entry |
| STRATEGY_MAX_OPEN_POSITIONS | 3 | cap; settings cannot exceed 3 |

Missing score, LP lock, authority revocation or holder concentration data rejects, even if score is 1 and LP is 100%. Active mint/freeze authorities reject too. Missing prices, transaction counts, liquidity or volume never pass. Pool age uses GeckoTerminal pool_created_at, stored as first_seen_at. The decision data timestamp is evaluation time for this cycle, not a claim about provider freshness.

Nominal notional = (current equity * 1%) / stop fraction; units = notional / quoted entry. Actual size is conservatively reduced so slipped entry + entry fee minus slipped stop proceeds after exit fee stays within the existing **1% inclusive-cost ceiling**, then bounded by current equity * RISK_MAX_POSITION_NOTIONAL_FRACTION / slipped entry in token units. Both nominal and actual size are recorded. FEE_PCT=0.25 and SLIPPAGE_PCT=0.5 are per-side defaults; estimates are round-trip at stop. Max estimated loss is not a guaranteed loss ceiling on gaps. Daily loss 3%, peak drawdown 10%, available cash and aggregate open-stop exposure still gate every executor open; no override weakens them. Missing current quotes for previously opened mints blocks new opens (WATCH) and reports unavailable equity rather than inventing marks. This milestone does not add a continuous price-monitoring/automatic exit service. Existing executor close retains losing trades.

Apply migrations 001, 002, then `supabase/migrations/003_decisions.sql` in the Supabase SQL editor. 003 adds private RLS-enabled paper_decisions with no public policies; anon/authenticated privileges are revoked. The existing single `persist_paper_cycle` RPC atomically persists cycle, decisions and ledger. Existing ledger history must remain a prefix of each new history; paper_trades uses conflict DO NOTHING, never overwriting losses. Only backend service role may call it. Never put secrets in SQL, source or chat.

The dashboard and /status show all latest-cycle decisions, proposals, equity, open paper positions and all closed paper trades. PAPER_DECISION, PERSISTED_CYCLE and CYCLE_RESULT log lines provide audit evidence. Offline tests in tests/test_strategy.py use fixtures/temp ledgers, never network.

## Volume profile confirmation (PAPER only)

`act.volume_profile` computes a **candle-derived approximation**, not trade-by-trade volume-at-price or order flow. Equal-width price bins span the observed low/high. Each candle's USD volume is distributed uniformly over its high-low range by overlap with each bin. A flat candle contributes entirely to its containing bin (the top endpoint is clamped to the last bin). An all-flat history has a single price level. Nonfinite, inconsistent, duplicate-timestamp, empty or zero-total-volume data fails loudly.

POC is the highest-volume bin's midpoint (ties choose the lower bin). Starting at POC, the contiguous value area expands one adjacent bin at a time toward the larger adjacent volume, ties downward, until at least the configured percentage is included. VAL/VAH are the outer edges; actual enclosed percentage may exceed the target and is recorded.

**Entry:** all existing hard filters and risk gates remain unchanged. OHLCV is fetched only after every hard strategy filter passes and equity is available. BUY then requires current observed price **at or above VAH**, with VAL strictly below entry. Inside value area is rejected pending acceptance; below VAL is invalidation and rejected. No profile, exhausted retries, malformed/stale candles or insufficient history means REJECT, never BUY. Hard-filter rejects explicitly say profile not attempted. Profile evidence is retained in each eligible decision's existing proposal JSONB, including unavailable reasons; no new database columns/migration are required.

**Stop/sizing:** stop = max(VAL, slipped entry * (1 - `STRATEGY_STOP_FRACTION`)); the tighter VAL stop is preserved. The fraction floor is anchored to the same slipped entry the paper ledger records, not the pre-entry quote. Nominal sizing is 1% equity / fractional stop distance, then reduced for fees/slippage so estimated stop loss remains within the existing cost-inclusive 1% gate, and capped at 2% equity in slipped-entry notional by default. Targets, concurrency, loss/drawdown gates and stale-quote protections are unchanged. Stops are not guaranteed fills: existing first-observing-cycle simulation fills still apply.

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


## Concentration fallback (2026-11-10)

This section updates the PR #3 concentration policy above. PAPER ONLY; no thresholds,
wallets, exchange APIs, paid services, signup or signing added. Authorities remain
onchain-primary, with explicit-null and disagreement rules unchanged.

### Real provider verification before implementation

Read-only JSON-RPC POST getTokenLargestAccounts, finalized, was tested against both
B9FgqHbtccsgcL6YPfUhNd81qveNtrTbsfLvcjxtpump and
6sGK9xX2wSsMiUTTo3xZBKe7fcydF3rtQnSGtKJMpump on 2026-11-10.
The official contract returns up to 20 ranked token ACCOUNT addresses with raw amount,
decimals and context: https://solana.com/docs/rpc/http/gettokenlargestaccounts.

| Endpoint | Captured observations | Adoption |
|---|---|---|
| https://api.mainnet-beta.solana.com | Both probes HTTP403, JSON error code403, Access forbidden | Existing endpoint retained; no successful largest response in this session |
| https://api.mainnet.solana.com | Both HTTP403, Access forbidden | Existing allowed endpoint; no new success |
| https://solana-rpc.publicnode.com | HTTP403, code-32602 Indexed requests require a personal token; second HTTP429 code-32005 Rate limit exceeded | Rejected: personal-token restriction, no signup |
| https://rpc.ankr.com/solana | Both HTTP403, API key is not allowed to access blockchain, code-32052 | Rejected: no working keyless response |
| https://solana.drpc.org | Both HTTP400, code35 chain is not available on free plan, please upgrade to paid plan | Rejected: paid restriction |
| https://solana.public-rpc.com | Navigation ERR_CERT_AUTHORITY_INVALID; POST failed to fetch | Unverified, not adopted |

No new provider produced a captured successful largest-accounts response, so **no
allowlist expansion or provider-failover setting is introduced**. Phase B stands alone.
These are browser observations, not universal quotas; earlier deployed RPC429 may differ
from browser403. No request-rate ceiling or permanent availability is inferred.
Existing RPC retries (1), shared per-cycle HTTP-attempt budget (24), success/failure cache,
and loud per-candidate errors remain unchanged. No unverified endpoint is silently used.

### RugCheck structure and limitations

Full reports were read at https://api.rugcheck.xyz/v1/tokens/{mint}/report for the two
mints above and So11111111111111111111111111111111111111112. AICoin returned 20
rows but totalHolders=6102: **a truncated ranked prefix, not a complete census**.
Each row has address, owner, raw amount, decimals, pct, uiAmount, uiAmountString,
and insider. Its first address J39A9AP1P2EhY9Zw7JUfe1R55AmMYAa2L5SudfhRiikb
matches candidate market liquidityA; owner is pool
CmKXFUCW8CSnmqTVHBrK3E6sZk4fLeeB24yia7GyXeWt. This corroborates token-account
semantics, NOT owner aggregation or beneficial-owner concentration. Both methods
therefore measure top N eligible token accounts, not distinct humans/wallet owners.
The 6sGK report returned topHolders=[] despite positive supply and a reported pool
balance; wrapped SOL returned topHolders=null and zero supply. Both are unavailable,
**never zero concentration**. Observed pct and uiAmount are not arithmetic inputs;
one AICoin uiAmount disagreed with amount/uiAmountString. Use raw integer amounts only.

Fallback computes 100 * sum(top N eligible raw account balances) / raw mint supply.
Exclude candidate-pool-owned token accounts and incinerator-owned accounts, plus literal
1nc1nerator11111111111111111111111111111111; never reduce supply by excluded amounts.
Candidate pool identity must match a report market pubkey, the matching mint side,
and that side's liquidity vault account owner/mint. Matching holder/vault amount and
owner must agree. Unknown pool/vault identity, duplicate/unsorted addresses, missing owners,
floats/bools/negative balances, invalid decimals, excess summed balances or invalid supply
are unavailable. N eligible rows must remain after exclusion; a shorter prefix is NOT
assumed complete. Fewer than N is accepted only when all listed balances sum to supply,
proving no positive balance remains unlisted. No owner-aggregated pct is substituted.

### Precedence, config and audit

| Setting | Default | Policy |
|---|---|---|
| SECURITY_CONCENTRATION_FALLBACK | rugcheck | off restores PR #3 no-fallback behavior |
| SECURITY_CONCENTRATION_TOLERANCE_PP | 0.5 | Absolute percentage points, bounded 0..1; not a threshold relaxation |

ConcentrationSettings in src/act/security.py inherits all existing SecuritySettings.
Onchain account concentration is PRIMARY whenever available. Valid normalized full-report
concentration is fallback only when primary is unavailable. Both valid values are always
compared, even with fallback off. Difference greater than tolerance rejects; crossing the
configured rejection boundary rejects even within tolerance. Equality to the 50% threshold
still rejects. Authority disagreements still reject without tolerance. Summary concentration
aggregates are not comparable and are ignored for this metric. Missing both rejects as
missing_security_data; disagreement and excessive concentration retain failed_security_check
(overlapping missing/failed labels may both occur on disagreement).

SECURITY_ONCHAIN_ENABLED=false still returns pre-onchain assessments unchanged, never a
bypass. The fallback cannot supply missing onchain authorities. Existing preliminary
liquidity/volume/age rejects skip security requests; skipped values remain unknown.
No extra RugCheck call is needed: the already-fetched full report is normalized.

security_evidence includes concentration_source (onchain/rugcheck/missing), raw primary
and fallback values, measure, raw numerator/denominator, exclusions, ranking size, errors,
tolerance and disagreements. security_source_stats includes concentration_by_source,
verified/passed/failed counts; dashboard Security provenance renders these existing JSON
fields and cycle SECURITY_SOURCES/CYCLE_RESULT logs preserve them. Decision proposal JSON
already persists security_evidence; **no Supabase migration**.

The fallback is provider-attested, not independently slot-verified onchain evidence.
RugCheck supplies no onchain context slot/freshness guarantee here. detectedAt is preserved
as provider metadata, not asserted to be the last holder refresh; observation time is recorded.
Snapshot drift may cause fail-closed disagreement, or unavailable/incomplete reports may
leave concentration missing. Coverage is not guaranteed and no forced BUY is expected.
Offline tests pin observed raw-holder projections and label synthetic mutations explicitly;
existing RPC budget/cache/read-only/retry and rejection-breakdown tests remain in place.
Provider failover tests/settings are inapplicable because no new provider was adopted.


## Results dashboard (presentation only, 2026-11-10)

The existing `/` service route is server-rendered HTML with inline SVG/CSS, no CDN, JavaScript dependency or frontend build. `/health` and `/status` remain read-only. There are no trading-state mutation endpoints. Strategy, security filters, thresholds, executor and safety guards are unchanged.

Six panels:
1. **Paper equity curve** copies the existing ledger-derived `paper_account` into `equity_snapshot` in each cycle report (equity, cash, position count, timestamp, valuation status). The existing atomic `persist_paper_cycle` stores this JSON; no migration. Snapshots start at the first cycle of this release, not before; no backfill. A single observation is a point; a flat portfolio stays flat. Null valuations create gaps, not zero or last-known marks. The exact timestamp/data table accompanies the chart.
2. **Open positions** uses existing persisted cycle `paper_positions`: entry, observed quote, net unrealized P&L, stop/target and distances, age, stale flag, source and observation timestamps. Explicit no-open-positions state. These are cycle marks, not streaming quotes.
3. **Trade history** uses the full `paper_trades` ledger view, never a winning-trade-only selection: entry/exit, net P&L, total fees, reason, timestamps and display-only holding duration. Slipped prices are recorded; separate historic slippage-dollar amounts / entry quote are not persisted and are unavailable, not reconstructed. No closed trades is explicit.
4. **Decision log** displays up to 200 recent candidates from 30 durable cycles, with token/pool, verdict, all reasons and missing fields, expandable per-field onchain/RugCheck/missing provenance and raw authorities/concentration values. Full stored proposal/discovery/volume-profile evidence remains inspectable.
5. **Risk / cycle health** shows ledger account exposure and cash, observed positions vs configured cap, RPC calls/budget, effective STRATEGY_* and SECURITY_* values plus immutable risk caps captured with that cycle (never secrets). Cycle timestamp/status and persisted source statistics/errors are visible. Explicit per-provider 429/retry totals were not persisted; they are unavailable rather than inferred from error strings.
6. **Cycle / rejection trends** shows the last 30 stored cycles, discovery seen/kept/dedup stats, multi-label rejection bars with missing_security_data distinct from failed_security_check, and expandable security coverage per field/source. Missing historic stats remain unknown, not zero.

### Data and free-tier limits

After each cycle the service makes one bounded read of `cycles.report` ordered by completed_at descending (limit 30), then serves the in-memory view without per-request provider/database calls. Durable reports retain snapshots beyond the display window. Decision HTML is capped at 200 candidates; details collapse raw evidence for mobile reading. All closed ledger trades remain visible. Local mode only keeps the recent cache for the process lifetime; Render sleeps/redeploys lose local files but configured Supabase reports survive. Until the first cycle completes, data is unavailable. A failed history read clears the history view, logs RESULTS_HISTORY_FAILED and shows a loud banner / health field; no synthetic fallback. Current account marks are as of the displayed cycle, not live market valuations.

No new environment variables, API keys, external services, paid resources or Supabase schema changes are required. Existing Render auto-deploy applies after merge. Offline results tests cover snapshot copying/null valuations, no backfill, flat and gap charts, bounded reads, all six sections, read-only routes, escaping/capping, stored rejection labels, duration and preservation of losses/fees; no network or lifespan is started by those tests.


## Strategy validation and readiness — evidence only

PAPER ONLY. The ninth dashboard panel computes and displays evidence; it does not
permit trading, change strategy semantics, or enforce any Phase 2 threshold.
The strategy does not read readiness values. No exchange APIs, keys, paid services,
optimization machinery, or backfilled observations are introduced.

### Reference gates and metric definitions

Named display-only constants in src/act/readiness.py are DISPLAY_MIN_TRADES=100,
DISPLAY_MIN_WEEKS=8, DISPLAY_MIN_EXPECTANCY=0, DISPLAY_MIN_PF=1.3 and
DISPLAY_MAX_DD_PCT=10; DISPLAY_REFERENCES contains the labels. The seven gates are:

1. Completed ledger trades >=100. A known count below 100 is a measured fail;
   an unavailable ledger is insufficient data, not zero.
2. Elapsed calendar weeks >=8 from BOTH the first retained persisted cycle and
   first recorded trade (open or closed). Both baselines are shown separately.
   This is calendar age, not eight weeks of continuous uptime.
3. Mean closed-trade realized_pnl >0 USD after the ledger's modeled fees and
   slipped execution prices. Use recorded net P&L directly: never subtract costs
   twice or reconstruct quotes. Missing recorded fees/prices/P&L is insufficient.
   Separate historical slippage-dollar attribution is not recorded.
4. Profit factor >1.3: sum of positive recorded net P&L divided by the absolute
   sum of negative recorded net P&L. Zero gross losses is undefined/insufficient,
   never infinity. Zero-P&L trades are neither wins nor losses. This cost-inclusive
   definition uses the closed ledger, not unrealized gains.
5. Maximum drawdown <10%: peak-to-trough decline on valid positive equity snapshots.
   At least two snapshots are needed. Coverage start/end/count are explicit;
   this is capture-window DD, not intracycle or pre-capture lifetime DD. Missing
   valuations and Render sleep gaps cannot be interpolated.
6. Out of sample: plan documented, awaiting data; always insufficient until an
   actual reserved-period validation study is completed and independently reviewed.
   This build does not turn the documented plan into a passing validation result.
7. Critical issues: zero recorded health flags in the retained cycle window.
   Exact proxy fields are status (non-success), error, strategy_error,
   learning_error, persistence.status=failed or persistence.error,
   collectors.*.status in failed/degraded or collectors.*.error, and nonempty
   position_management.stale_tokens. Missing cycle status prevents certification.
   This cumulative historical proxy has no issue-resolution registry; it neither
   establishes that old flags remain unresolved nor certifies absence of security,
   manual-review, missing-cycle or unrecorded issues. The current atomic write
   cannot certify its own successful persistence.

Every metric carries n, data start and coverage caveats. Expectancy/PF gate
comparisons require at least two observations; this minimal descriptive floor
is NOT statistical readiness, and does not replace the separate 100-trade gate.
Win rate, duration min/mean/median/max (closed minus opened hours), exact exit
reason counts, and open positions/unrealized P&L are context, not extra gates.
Open-position P&L is never included in closed expectancy or PF. SMALL-N warnings
apply to every aggregate; no annualization or extrapolation is performed.

### Forward-only persistence and replay

After results capture, capture_readiness reads existing persisted cycle projections
and the current complete ledger. It saves the new readiness object inside the
same existing atomic persist_paper_cycle JSON RPC; no migration is required.
There is no historical readiness backfill or strategy dependency. The panel shows
at most 30 recent readiness summaries; underlying retained cycle records remain.
Remote history is paged in batches of 100 with a 10,000-cycle safety cap: reaching
that cap fails loudly instead of silently computing a truncated lifetime metric.
Local mode uses retained sibling report files and is ephemeral on Render.

Each snapshot records its version, compute time, record IDs, projected field list,
exclusive history cutoff, coverage window and SHA256 canonical JSON hashes for
cycles, trades and positions. To replay, retain the raw current report and listed
historical projections, then call compute(current_report, projected_cycles,
computed_at) using the recorded version. The current persistence field is normalized
to pending_atomic_commit before hashing so a later successful write does not alter
its input identity. Historical projections and full immutable ledger values must
match the hashes; dashboard summaries alone are not sufficient replay inputs.
READINESS_STATS logs computation time and gate states; READINESS_FAILED and
readiness_error expose collection/computation failure without substituting data,
changing strategy decisions, or introducing a trading gate. Tests are synthetic,
offline and separate from production records.

### Prospective walk-forward / out-of-sample study plan

This is a plan, not validation and not an optimizer. In this build NOTHING is
re-fit. Before any future study, pre-register the split dates, endpoints, sample
requirements and analysis code/config hashes without viewing reserved outcomes.
First accumulate at least the reference 100 completed paper trades and eight
calendar weeks; those counts alone do not guarantee enough independent samples
for the following longer study. Use rolling eight-week training windows followed
by two-week reserved OOS windows, with at least three nonoverlapping OOS folds
and an additional untouched final two-week holdout. Extend collection if per-fold
sample counts or label completeness are inadequate; never manufacture coverage.

Use immutable learning-session decision times for assignment. Train only on
sessions whose longest seven-day outcome has matured before the training cutoff;
apply a seven-day purge/embargo at boundaries and exclude token/pool identities
shared across train and reserved cohorts from training to limit leakage. Report
both the excluded population and all missing/late labels. Reserved sessions may
be scored only by the pre-registered frozen version; they cannot be used to pick
parameters, retry a favorable split, or choose an exit after observing outcomes.

A separately reviewed future proposal could fit descriptive calibration or
candidate-scoring parameters ONLY on training sessions. No such model or retraining
is implemented here. Freeze strategy rules, thresholds, security/preflight filters,
risk/sizing, fee/slippage assumptions, data sources and code version for each OOS
period and the final holdout. Any change starts a new prospective study/version.
A study manifest must retain split/session/trade IDs, hashes, exclusions, model
artifacts if ever introduced, missingness, code/config versions, and exact net
paper-trade outcomes. Hypothetical learning returns for rejected candidates are
not executable fills and must not be pooled into realized paper expectancy.
Report each fold and the final holdout separately, net costs, DD capture windows,
small-n uncertainty and selection bias; independent review, not this panel,
determines whether evidence satisfies the owner's real-money prerequisites.

Retained-data audit: per-cycle full decision/session/outcome events, decision and
cycle IDs, verdict/reasons, discovery/TA evidence where captured, baseline and
observation timestamps, horizons/due times/lateness, prices/liquidity/volume and
paper ledger/equity records support a future reproducible descriptive study.
Use durable cycle events, not only the bounded active/recent learning checkpoint.
They do NOT yet prove adequate sample size or complete coverage. Provider quote
timestamps can be absent; preflight selection limits captured TA cohorts; sleeping
free-tier cycles, follow-up budget/backlog caps and unavailable quotes create
nonrandom missing outcomes. There is no tick/order-book reconstruction, historical
slippage-dollar attribution, live fill/market-impact evidence or completed OOS
validation. Report these gaps rather than backfill or claim an unbiased study.

The immutable provenance pattern continues the prior read-only HFTENGINE review;
no HFTENGINE code was copied and that repository is not modified.


## Position-notional bound and sizing regimes

**PAPER ONLY.** New proposals use the smaller of existing inclusive-cost 1%-at-stop sizing and
`equity * RISK_MAX_POSITION_NOTIONAL_FRACTION` in executed (slipped-entry) USD notional.
The environment variable defaults to **0.02**, using the existing prefix-based settings style;
nonfinite values, zero, negative values and values above one fail validation loudly.
At the owner-reported equity of $9,411.30 this is $188.226 of principal, before modeled fees.
The cap applies before the risk gate/executor/ledger; these downstream components receive
the bounded token size. Proposals record the fraction, cap, slipped-entry notional and
principal-plus-entry-fee worst-case bound. For a fully funded long falling toward zero,
loss is bounded by its purchase notional plus modeled entry fee, not by its stop price.
The dashboard shows the recorded cycle cap and fee-qualified per-position bound; old
cycles/positions without recorded values are n/a, never backfilled with today's settings.

The stop fraction branch now uses slipped entry E: stop = E * 0.85, unless VAL is tighter.
For quote $0.0003818 and 0.5% entry slippage, E = $0.000383709 and its fraction stop is
$0.00032615265 (not the old quote-anchored $0.00032453). This is price-anchor consistency,
not a promise of a fill at that price or a promise to exhaust the 1% risk budget when capped.
Targets remain quote-anchored and unchanged.

### Rationale (owner-provided completed forensic findings)

The first paper trade, GTA6KODAK, closed November 12, 2026 at 13:06:46 UTC via stop_loss,
net -$588.70 (5.89% of equity versus designed approximately 1%). It went unmonitored for
approximately 22 hours because of Render free-tier downtime and both quote providers
exhausting HTTP 429 retries. The first retrievable valid quote was approximately 95.7%
below entry. The owner's forensic decomposition was approximately $94.58 entry-to-stop,
$492.38 stop-to-first-retrievable-quote, $0.13 exit slippage and $1.60 fees. A stop is a
trigger, not a floor; changing its threshold or cycle cadence does not bound gap loss.
The owner delegated the sizing choice and retains approval/veto of commits, PR and merge.

### Honest history

- **Regime A:** from the first trade on November 11, 2026 until this change's deployment:
  1%-risk-at-stop sizing, with no independent position-notional cap. Cash/risk gates still applied.
- **Regime B:** from deployment of this change onward: the same risk sizing with the
  additional default 2%-of-equity notional cap and corrected slipped-entry fraction stop.

Existing trades, append-only ledger events and losses are not rewritten. Per-trade
percentage returns remain useful across pure size changes, but dollar-based history
mixes regimes; aggregate expectancy/profit-factor comparisons are not asserted invariant
to differing per-trade sizes. Readiness/learning computations and thresholds are unchanged.
No stop/target fraction (0.15/0.40), maximum positions (3), hard filter, volume-profile
acceptance, cycle cadence, max-age, retry, executor, persistence or dashboard data rule
is relaxed. No real-money capability, fabricated data, paid service or API key is added.
HFTENGINE's previously adopted immutable input/decision/provenance snapshot pattern
is unchanged; no code is copied from it and that repository is not modified.
