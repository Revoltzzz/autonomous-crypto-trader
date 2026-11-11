# Learning foundation — paper-only, evidence first

This records labels, not trading policy. Strategy, preflight, OHLCV eligibility,
security gates, execution, position management and safety settings are unchanged.
No threshold selection or automatic calibration is implemented. All observations
are public keyless quotes, not orders or real-money capabilities.

## Durable schema and replay

The existing `persist_paper_cycle` RPC stores arbitrary `cycles.report` JSON,
alongside the existing paper ledger snapshot. Local mode uses the same cycle JSON.
No migration is needed. `report.learning` contains schema-versioned `state`,
append-only per-cycle `events`, stored `distributions`, `calibration`, `stats` and
collection `config`. A new cycle reads the latest non-null learning checkpoint;
transport/corrupt-schema errors are loud, never interpreted as a fresh start.
A cycle lacking learning after an error does not erase the preceding checkpoint.

Every evaluated BUY/REJECT/WATCH still has its original immutable decision and
proposal/intelligence block in its original cycle. Learning adds a reference
`learning_session_id` to each evaluation. A mint's first evaluation starts one
session; subsequent evaluations reference it rather than resetting its horizons.
The session retains original decision/cycle IDs, verdict, reasons, pool, baseline
price/liquidity and baseline quote source. Existing TokenDiscovery does not retain
a provider quote timestamp: `quote_at` remains null/unknown, with explicit status,
learning baseline capture time and original cycle start as collection context.
Pool first_seen_at is not misrepresented as quote time. Follow-up events retain session,
mint/pool, due and observation timestamps, lateness, source URL/parameters,
request/fetch timestamps, HTTP status, each attempt and returned evidence. This
continues the immutable-input/decision/provenance replay pattern reviewed from
HFTENGINE in PR #6; no HFTENGINE code is copied or modified.

Labels begin only at deployment: no historical decisions or returns are backfilled.
Replay uses the referenced original cycle and stored event values, not new quotes
or recomputed TA. The provider's response values are retained in the observation;
raw HTTP response bodies are not retained. Existing durable cycle records remain
after the dashboard window rolls off.

## Scheduling, resources and failures

Horizons after first evaluation: **1h, 6h, 24h, 72h, 7d**. Follow-up work happens
post-decision in the existing cycle, never during candidate evaluation. No new
scheduler, worker, always-on service or paid dependency is introduced.

| Environment variable | Default | Allowed collection bounds |
| --- | ---: | --- |
| `LEARNING_FOLLOWUP_BUDGET` | 6 | 0–20 HTTP attempts per cycle, both sources combined |
| `LEARNING_BACKLOG_CAP` | 200 | 1–1000 active first-evaluation sessions |
| `LEARNING_GRACE_SECONDS` | 3600 | 0–86400 seconds after due time |

These are collection controls, not strategy or return thresholds. Invalid values
fail loudly. Oldest due horizon is processed first (stable decision-ID tie break).
Each primary/fallback request consumes one budget unit. Request timeout is five
seconds per HTTP phase; there are **zero same-source retries**. At most one GT
and one Dex attempt per horizon. A 429 blocks that source for the rest of the
cycle, without blocking the other. Fallback is used for unavailable/error/429;
a successful partial observation or provider-index absence is terminal for that
horizon. Missing pool identity produces explicit data-missing evidence.

After the grace window, store `missed_service_gap_or_budget` with lateness and no
price/return; do not query today's quote pretending it was historical. Budget
exhaustion leaves unattempted horizons pending until a later cycle or expiry.
At active-cap overflow, retain the newest sessions and label all remaining horizons
of evicted oldest sessions `missed_backlog_cap`. Latest 200 terminal sessions
are retained in the display checkpoint. A mint-to-first-decision identity index
is retained indefinitely so eviction never silently restarts a mint; this index
is small per mint but grows with unique mints (not a bounded lifetime database).
The dashboard shows at most 200 sessions and 30 stored cycle distributions. Raw
cycle/session/event records are not deleted by these display limits.

The code logs `LEARNING_STATS`, `LEARNING_SOURCE_FAILED` and `LEARNING_FAILED`.
Stats include request budget usage, calls per source, pending/terminal counts,
status counts and learning elapsed milliseconds. The checkpoint requires one
additional Supabase read, and is saved atomically with the existing cycle RPC.
It is never written separately ahead of the report. Source failures become
availability labels; checkpoint/persistence failures are explicit cycle failures.

## Quote source verification (2026-11-11)

Read-only real keyless browser requests returned price, pool liquidity and h24
volume for these recent candidates (no signup or keys):

- mihao mint `4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump`, pool
  `Ho1aSG6WGH1FfXdEHrK2f4Ggt1Hoxo9s5jcLMDqeyeBz`:
  [GT pool](https://api.geckoterminal.com/api/v2/networks/solana/pools/Ho1aSG6WGH1FfXdEHrK2f4Ggt1Hoxo9s5jcLMDqeyeBz),
  [Dex pairs](https://api.dexscreener.com/token-pairs/v1/solana/4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump).
- HOLD mint `4iJ7xEQrUvVUQH9KVStvw3FaY2PZDd7RtR4L9zx2pump`, pool
  `GUsid5mPW6KiExjQDDggoTHrFv4ecGqqAgYwiHa4Ujbb`:
  [GT pool](https://api.geckoterminal.com/api/v2/networks/solana/pools/GUsid5mPW6KiExjQDDggoTHrFv4ecGqqAgYwiHa4Ujbb),
  [Dex pairs](https://api.dexscreener.com/token-pairs/v1/solana/4iJ7xEQrUvVUQH9KVStvw3FaY2PZDd7RtR4L9zx2pump).

Verdict: GT exact-pool lookup is a low-cost primary. Dex returns multiple pairs
in one token lookup and is a usable fallback only after matching chain, exact
pool and base mint. Do not choose a different pool or reciprocal quote. There is
no claim of guaranteed availability, guaranteed freshness or free-tier capacity.
GT 404/Dex no matching pair is `pool_not_found`, scoped to that provider's index,
**not proof the on-chain pool died**. HTTP errors/429/identity mismatch are distinct
from index absence. `pool_exists` is provider-observed existence, not chain state.
`liquidity_drained` is true only for an observed exact zero; no drain threshold is
selected. A missing/index-absent pool never gets a fabricated -100% return.

## Stored distributions and calibration views

Per cycle, derive solely from that cycle's stored decisions/discoveries/assessments
and labeled events: TA count/min/median/max/missing, market-field coverage, security
value counts by effective source and raw onchain/RugCheck source values (including
missing), LP-lock distribution/source, rejection reason
counts, field missingness, and return count/min/median/max per horizon. The UI
renders these stored aggregates instead of recalculating them on each request.
Calibration by rejection reason uses active plus latest 200 terminal sessions;
every median has n and missing counts. Overlapping reasons are not causal effects.
The table is descriptive, not a recommendation, and is explicitly small-n warned.

Returns are `(observed USD price / baseline USD price - 1) * 100`; liquidity change
is analogous. The baseline is the decision's existing discovery quote, not a new
synchronous tradeable fill. Its source and collection context are preserved, but quote age is unknown;
provider sampling differences, fees/slippage and lack of executable liquidity
mean these labels are **not realized strategy expectancy**. Missing values are
null, never neutral zeros. Actual observation time/lateness is always retained.

## Limits and later owner decisions

Free-tier sleeps, rate limits, oldest-first budget allocation, active-cap eviction,
provider coverage and first-evaluation dedup create non-random missingness and
selection bias. TA remains available only for unchanged preflight survivors.
No claims about filters earning their keep or positive expectancy are justified
from tiny samples. Pending horizons are an honest empty state, not failure or
fabricated outcomes. The foundation accumulates replayable labeled evidence for
later owner-reviewed threshold selection, expectancy studies and walk-forward
analysis; those studies, threshold choices and Phase 2 gates are not implemented.

## Offline validation

Run `ruff check .` and `pytest`. Learning tests use frozen times and HTTP
MockTransport only: all verdicts, dedup, horizon boundary/order, budget counting,
sleep gaps, cap eviction, 429 isolation, missing vs provider absence, identity
matching, zero liquidity, partial values, checkpoint continuation, distributions,
config bounds and escaped empty dashboard. Existing strategy/execution tests
remain regression checks, without network access.
