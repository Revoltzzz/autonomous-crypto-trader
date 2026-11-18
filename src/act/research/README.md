## Build 4 part 5 — partial persistence increment, dispatch OFF

PAPER ONLY. Priority 1 is NOT complete. Checkpoint capture now calls the existing effective-observation and append-only migration helpers inside the cycle checkpoint path. Legacy identity/cost events remain an unchanged prefix; champion pointers stay at the same v1 IDs. Unchanged observations are idempotent; changed observations append children. Capture/validation errors propagate, not silently downgrade. These remain incomplete observations, NOT resolver-complete policy snapshots. No historical checkpoint is rewritten.

Runtime decisions still use hardcoded paths. No persisted-champion dispatcher or ACT_DISPATCH_MODE switch is wired. ACT_POLICY_DISPATCH=false is the existing prerequisite-helper default; true raises DISPATCH_BLOCKED and cannot activate trading. Do not treat that helper as a production kill switch. Independent all-market dual-path byte equivalence is still missing; dispatch must remain OFF. Existing same-evaluator Solana roundtrips are not independent evidence. Futures remains blocked_http_451.

New offline persistence integration tests cover initial write/idempotence, legacy event prefix and champions, environment-change append, and loud capture failure. CI ruff + pytest is the validator; browser-only development, no local Python or shell execution. Environment values are resolution-time observations, not cycle-frozen inputs. Fixture evidence is small-n, bounded and non-exhaustive; no profitability claim.

Still required: complete named rules including dynamic exchange minimums, selection/entry/exit/sizing/costs and effective bounds; resolver acceptance; independent Solana/Spot/Futures-limited corpus covering all vetoes and boundaries; full snapshot migration; persisted-pointer resolution and per-market fail-closed runtime dispatch with a real kill switch, gated on green proofs. Shadows, attribution/scoreboard, backtester, OOS and promotion remain deferred. No risk, scanner/TA, fill-model, exchange accounts/keys/signed endpoints, geo-block, live-execution, Render/billing or HFTENGINE changes; no external code copied.

## Build 4 part 4: PARTIAL dispatch prerequisites — NOT activated

PAPER ONLY. Priority 1 is **not complete**. research/dispatch_safety.py records
resolution-time observations from the actual StrategySettings, RiskSettings,
SecuritySettings, DiscoverySettings, PositionSettings and VolumeProfileSettings
classes, both existing risk-limit dataclasses and Binance fee_rate functions.
Every field of those six models includes its effective value, schema bounds and
environment-variable/default source. Solana costs come from the supplied resolved
Settings instance; no persistence credentials or arbitrary environment dump is stored.
Implementation source fingerprints detect code changes but are NOT semantic rule
capture or proof of equivalence. Hardcoded entry/exit/TA/fill constants and
instrument-specific exchange minimums are not yet fully modeled. Environment
observations are resolved when called, not frozen through the production cycle.

### Evidence and activation boundary

Offline tests include 20 Solana effective-StrategySettings roundtrip cases covering
BUY, REJECT, preflight WATCH and selected threshold boundaries. Full decision
objects are compared as canonical JSON bytes: IDs/time are pinned in fixtures,
not stripped; floats are not rounded. A mismatch raises
DECISION_BYTE_DIVERGENCE with case name and both hashes. This is **the same
production evaluator with implicit vs captured config**, not an independent
policy-dispatched path. It does not prove all vetoes, exits, sizing gates, Spot or
Futures equivalence. Comparator tests are not strategy evidence. Small-n fixture
coverage is descriptive, not profitability or exhaustive behavior evidence.

Runtime dispatch is **OFF and unwired**. All existing hardcoded v1 paths remain
unchanged; Futures remains blocked_http_451. The reserved ACT_POLICY_DISPATCH
flag defaults false in the prerequisite guard. false returns hardcoded_v1;
true raises DISPATCH_BLOCKED, and other values are invalid. **It is not yet a
production kill switch**: setting it cannot activate dispatch or change the cycle.
No CI result or caller-supplied observation can bypass the missing implementation.

### Append-only observation upgrade, not full snapshot migration

migrate_observation(state, effective_observation(settings), at) is a pure,
explicit transform. Validate the existing checkpoint first; append one
content-addressed child observation per market; keep every legacy event and
champion pointer byte-for-byte. An unchanged observation is idempotent. Changed
observations append new children without rewriting old versions. The returned
checkpoint can be persisted in research_registry using the existing cycle store;
no new database schema or remote write interface is added. Offline tests cover
local persistence roundtrip, corrupt indices/pointers, tampering and drift.
**The running checkpoint hook does not call this helper.** No deployed or stored
checkpoint has been migrated by this change. Legacy champions stay identity-only
and the full resolver continues to reject them loudly. Observation schema
act.effective-observation.v1 is deliberately distinct from act.policy.v1;
partial observations must never masquerade as complete snapshots.

### Required follow-up and retained limits

Before activating anything: finish named complete effective rule capture with a
single shared resolution path; capture instrument minimums as actual runtime
inputs; build a policy-backed evaluator and full all-market accept/reject/veto,
boundary, sizing and exit decision-byte corpus; implement append-only full
snapshot attachments preserving v1 champions; wire persisted-pointer resolution,
market-halting drift failures and a real kill switch only after CI proves parity.
Proof remains fixture-bounded, never exhaustive. Shadows, attribution/scoreboard,
executable approximate backtester, one-shot OOS and promotion remain deferred.
No risk limits, scanner/TA algorithms, fill models, live execution, exchange
accounts/keys/signed endpoints, geo-block work, Render or billing changes.
HFTENGINE was not accessed or modified and no external code was copied.
CI (ruff + pytest) validates this increment; no local shell/Python runs claimed.

## Build 4 part 3: partial policy primitives (not activated)

PAPER ONLY. This increment adds research/policy.py: canonical immutable policy
values with explicit selection, entry, exit and costs sections; registration via
the append-only content-addressed registry; and a standalone persisted-champion
resolver that rejects pointer mismatch, missing markets, legacy identity-only
records, implementation mismatch and configuration drift. Exports are copies.

These are caller-supplied snapshots, NOT verified complete production settings.
Nonempty sections do not prove semantic completeness. The byte fixture proves
canonical snapshot round trips only, NOT equivalence to current hardcoded v1
strategy decisions. Runtime dispatch and checkpoint migration are NOT activated;
legacy champions and all paper decisions remain unchanged. No historical trade
is attributed, and no promotion or pointer-switch interface is added.

Still deferred in priority order: capture all effective v1 selection/entry/exit
settings and prove decision-byte equivalence before wiring runtime dispatch;
bounded parameter/news challengers and same-data forward shadows; cumulative
version attribution and escaped 375px-safe scoreboard; executable APPROXIMATE
kline backtesting with next-candle timing, fees, minimums, gaps and honest
insufficient_data/451 paths; durable one-shot OOS, evaluation journal and gated
actuator. Phase 2 requires the challenger's own >=100 trades, >=8 weeks, positive
net expectancy, PF>1.3, mark-to-market DD<10%, walk-forward consistency and
completed backtest/forward-shadow evidence. Missing evidence never passes.
Tests in test_research_policy.py cover the primitives only; CI is the validator.
No shell/Python runs are claimed. No risk, scanner/TA, fill model, exchange,
Render/billing or HFTENGINE changes. No external code copied.

# Build 4 — partial research foundations

PAPER ONLY. Zero promotions. This is NOT the full Build 4 agent upgrade. Existing paper selection, fills, risk limits and champions remain unchanged. No keys, accounts, signed endpoints, live orders, paid services, Render or billing changes.

## Delivered

- registry.py: append-only content-addressed strategy records, copied exports, same-market parents and non-overwritable baseline champion pointers. baseline_registry(created_at) supplies identity-only v1 definitions for Solana, Spot and Futures at bc1376880417592c82fbde6eff2c6e1606bb162a. Actual configuration snapshots/activation are NOT captured. Caller must save/restore registry events, not regenerate IDs each cycle. Promotions are disabled, even when evidence evaluates eligible.
- replay.py: completed-candle-prefix decision replay, symbol/version/range metadata, gaps without interpolation, adapters for Binance and retained Solana OHLCV, existing bounded Spot vision public read (60 bars), explicit known Futures 451 skip. Decisions see only closes <= T, not incomplete bars. Callbacks must be pure: the API cannot sandbox unrelated future data.
- Disjoint chronological in-sample/holdout split definitions; single-use holdout keys. Callers must durably persist keys even on evaluation failure. This is not durable walk-forward orchestration or an optimizer.
- evidence.py: explicit-version forward-paper attribution math, net realized P&L after costs, expectancy, PF, closed-trade equity DD, n, small-n and ranges. No old trade gets a guessed version. Closed-trade DD is NOT mark-to-market DD; zero-loss PF stays undefined.
- Promotion gate evaluator: >=100 own forward-paper trades, >=8 weeks, positive after-cost expectancy, PF>1.3, independently supplied mark-to-market DD<10%, walk-forward consistency, completed backtest and forward shadow evidence. Per-gate results/reasons returned; never promotes. Inputs remain caller-trusted until provenance orchestration is implemented.
- News context retains actual verified/unverified items and all source fetch provenance. Every supporting source must be fetched strictly before the decision cutoff, so later corroboration cannot leak backwards. Exact title tokens are relevance hints, not identity confirmation. No sentiment fabrication.
- runtime.py consumes actual News Agent output after existing paper decisions each cycle, stores research_news in existing cycle JSON, and records failures loudly. It never changes strategy/risk/execution. Current-cycle Solana decisions usually predate news fetch: empty contexts are honest. Snapshot absence is not proof of no relevant news.
- Shadow record constructor labels shadow/ledger-ineligible; not a forward runner. Offline fixture tests cover boundaries. CI is the validator; no local test execution was available.

## Explicitly deferred

1. Executable cost-complete historical P&L backtesting, including Solana entry/exit replay reusing execution fees/slippage and minimums, arbitrary historical range ingestion and cost-model tests. Decision replay deliberately returns insufficient_data, zero trades and no fills. Existing Binance fills require contemporaneous depth; historical klines cannot establish spread/impact/executable fills. No invented fills or Phase 4+ L2/latency replay implementation.
2. Durable active parameter/config registration, all-market challenger generation (including improved Solana new-coin entry/exit and news variants), actual forward shadowing, persisted evaluation/promotion-decision journal, champion promotion actuator, fully enforced durable one-shot OOS evaluation.
3. Per-version ledger/scoreboard integration, pending-horizon upgrade, holding-period news attribution, historical news archive and cumulative per-version evidence. Existing horizons remain unchanged. Current-cycle news recording is continuous, but is NOT yet news-informed strategy adaptation. No champion edited from headlines.
4. Champion/challenger HUD, per-gate history and matching daily-digest status summary. Existing PAPER MODE / LAST STORED STATE unchanged; no new 375px panel claim. Root README remains unchanged; this module README documents the partial lifecycle.

## Honesty limits

Conservative headline corroboration is NOT fact-checking. Unverified stays unverified. Mainstream headlines may not cover brand-new Solana coins. Exact-token relevance is low recall and does not establish token identity. No fabricated sentiment scores.

Public-data backtests, once implemented, are simulations, not proof of future performance. Survivorship/discovery selection bias, history gaps, free-service sleep and missing evidence remain visible. Future forward shadowing accumulates at startup/~20-minute cycle cadence while awake, not tick speed. Small-n is descriptive only. Futures remain unbacktestable while geo-blocked.
