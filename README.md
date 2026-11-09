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
