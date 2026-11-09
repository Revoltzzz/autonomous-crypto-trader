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
