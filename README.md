# Autonomous Crypto Trader

> **PAPER TRADING ONLY.** Research and simulation only. No real-money execution, exchange credentials, wallet keys or signing. Users must place any real trades manually outside this software. Simulated results are not investment advice.

## Architecture
Collectors -> validated domain models -> risk limits -> paper executor -> orchestrator reports. The cycle collects CoinGecko prices, new Solana pools and RugCheck assessments; it never automatically opens even paper trades. The executor is a separate explicit simulation interface. Unknown security data stays unknown. Network and parsing failures raise errors and are written into a failed cycle report, never replaced with invented values.

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
No API keys are required by Milestone 1. SUPABASE_URL and SUPABASE_SERVICE_KEY are reserved for a later milestone and may remain empty. Optional environment overrides: PAPER_ONLY (must be true), LEDGER_PATH, REPORT_DIR, HTTP_TIMEOUT, HTTP_RETRIES, FEE_PCT, SLIPPAGE_PCT. Never commit .env or secrets. Tests use mocked HTTP transports and temporary ledgers, not the network.

The local ledger is an append-only event history in JSON, rewritten atomically without deleting events. Back it up; it is single-process storage, not a concurrent database. Size means token units; prices and account values are USD. Entry and exit fees are charged on slipped notional. Risk checks include projected stop slippage and round-trip fees, aggregate open stop risk, daily loss and peak-equity drawdown. Only fully funded long positions with a stop below entry are permitted. Cash and equity include simulated fees. A stop is an invalidation assumption, not a guaranteed fill.

GeckoTerminal discovery page: https://www.geckoterminal.com/explore/new-crypto-pools/solana . The older /solana/new_pools web route is not used. The API /api/v2/networks/solana/new_pools is treated as unverified; any request/schema failure explicitly requires endpoint re-verification. No empty-success fallback. RugCheck unknown fields are explicitly listed in risks and never interpreted as safe. Collector scoring is provider-reported and not a promise of safety.

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

The `/` dashboard shows the latest real cycle: BTC/ETH/SOL USD prices, up to ten newly discovered Solana pools, security assessments, paper equity, and explicit collector success/failure/skipped outcomes. `/health` reports uptime, paper-only mode and the last cycle timestamp. `/status` exposes the full latest cycle JSON. A cycle starts at startup, then repeats every 20 minutes after completion while awake; results are appended to `reports/cycles.jsonl` and printed as `CYCLE_RESULT` / `COLLECTOR` log lines. The orchestrator stops at its first collector error: downstream collectors are honestly marked skipped, not successful. The unverified GeckoTerminal endpoint still requires re-verification on request/schema failure. Nothing submits trades, including paper trades.

**Free-tier caveat:** Render may spin down idle free services, so this is not an always-on scheduler. Sleep pauses research, and waking starts a new cycle. Memory, local JSONL reports and the simulated ledger are ephemeral and may be lost on restart/redeploy; initial paper equity comes from the ledger, not live funds. No paid persistence is configured. Save important reports externally before a restart. Missing or failed data is never fabricated or interpreted as safe.
