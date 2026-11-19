## Build 4 part 11 — PARTIAL Solana sizing-region equivalence (PAPER ONLY)

This section supersedes older deferred-sizing statements below; older sections are historical increments.
Browser-only GitHub UI, no checkout/shell/Python and no local test runs. CI (ruff + pytest) is the validator.

The isolated evaluate_sizing consumes the captured act.strategy.evaluate slipped-entry assignment and contiguous six-assignment sizing region: equity times captured 0.01 risk fraction; nominal stop-distance notional; fee/slippage-inclusive entry cost and net stop proceeds; equity times captured RiskSettings.max_position_notional_fraction executed-notional cap; minimum of nominal, funded-risk and cap quantities, followed by the captured (1 - 1e-10) haircut. It consumes captured fee_pct/slippage_pct values (converted to fractions), model cap fraction, rule bodies and explicit not-applicable Solana instrument-minimums marker. Exact AST topology/formulas are pinned; drift or missing bodies/values fails loudly. Entry/stop and equity are caller-supplied upstream state, NOT frozen market observations.

Sizing corpus: Solana n=13 synthetic six-field canonical-byte comparisons against locals observed from the UNCHANGED production evaluate call (test-only return profiler, restored in finally). Complete sizing-region mapping is risk_budget, nominal_notional, entry_cost, stop_proceeds, notional_cap, size. No float rounding, approximate assertions, field deletion or production arithmetic calls in the interpreter; unified diff on byte mismatch. Tests additionally assert production proposal size equals the interpreted size. This is NOT complete DecisionRecord/proposal-byte or upstream stop-construction equivalence. Cases cover binding cap, nominal/risk tie, fee-inclusive binding risk, slippage and both costs, tiny positive quantity, nextafter entry/stop neighbors and actual computed float cap-switch threshold plus its neighbors. The 0.01 and 1e-10 production constants are retained exactly as Python floats; no approximate decimal boundary is substituted. Haircut is pinned, not exchange-step rounding.

Solana has NO exchange lot/minimum-quantity/min-notional gate or quantity-step rounding in this captured sizing region; instrument minimums are explicitly not_applicable, not a missing exchange observation. Minimum-quantity edges are therefore N/A, not invented. Zero equity/cap exhaustion is outside the positive-sizing subset and fails loudly, rather than manufacturing an accepted zero-size trade. Prior Solana veto comparisons remain separate: early n=6, funded n=8, remaining n=21 (n=35 prior). New invalid-cost veto n=6, so veto n=41; new sizing n=13; combined n=54. Spot veto/sizing n=0: deferred, only caller-supplied instrument scope/values, synthetic fixtures would not be observed exchange evidence. Futures veto/sizing n=0, blocked_http_451/nontradable; no instruments invented.

Safety tests cover sizing formula/min/haircut/slipped-entry drift, wrong market, dispatch ON, missing capture body/value, unexpected minimums, zero equity, nonfinite input, stop equal to entry and missing stop. No full strategy acceptance, screening, profile-stop construction, post-gate reporting, executor sizing/rounding or exits are interpreted. A separate evaluate_invalid_cost now interprets the captured first check_trade invalid-cost guard, helper and veto reason, with n=6 complete RiskCheckResult byte fixtures: fee/slippage just below zero, equal to 100 and just above 100, preceding leverage/missing-stop vetoes. Valid-cost continuation stays loud unsupported; existing interpreters are unchanged. Nonfinite-cost corpus is not claimed (canonical snapshots reject nonfinite JSON). No required sizing-region body/value capture gap was found by source inspection; missing captures are explicit CAPTURE_GAP failures, not approximations. Resolution-time NOT cycle-frozen, custom config and unauthenticated fingerprint limitations remain. Small-n synthetic fixture equality is NOT live-data equivalence, observed market evidence, exhaustive proof or profitability evidence.

No production risk/scanner/TA/fill changes, live execution, exchange accounts/keys/signed endpoints/orders, geo-block work, Render/billing or HFTENGINE changes; no external code copied. Dispatch remains OFF, unwired, no activation path or authorization. Merge requires a separate approval after green CI.

## Build 4 part 10 — PARTIAL remaining Solana risk veto corpus (PAPER ONLY)

Browser-only GitHub UI; no checkout/shell/Python or local test runs. CI is the validator.
New isolated evaluate_remaining_risk consumes the captured per-trade-risk, daily-loss/projected-exposure and drawdown/projected-exposure guards, assignments, limits and formatted veto strings. Exact tail AST (including reasons and ordering) is pinned; mutations fail loudly. It delegates only to the isolated early/funded interpreters, never production decisions; production check_trade is unchanged. Earlier interpreters retain their original reason-consumption behavior.

New synthetic Solana n=20 byte-comparison fixtures (17 zero-cost boundary/precedence cases plus 3 default-cost cases), plus an inclusive drawdown comparison safety case: 21 new comparisons; with prior n=14, Solana n=35. Spot n=0 (future scope remains caller-supplied instrument values only); Futures n=0, blocked_http_451/nontradable. Full canonical RiskCheckResult bytes compared with unified diff on mismatch. Boundaries use exactly representable zero-cost thresholds and math.nextafter, not approximate decimal thresholds. Early -> funded -> per-trade -> daily -> drawdown precedence is exercised. Safety covers unsupported acceptance, tail guard/reason mutations, missing/nonfinite input, wrong market and dispatch on.

Capture inventory retains the full check_trade definition and resolved Solana RiskLimits; no missing tail-body capture was found by source inspection. This is risk arithmetic, NOT sizing. Invalid-cost veto, complete accepts, sizing/caps/exchange minimums, strategy BUY/WATCH/exit/full-market corpus remain unsupported/deferred. Unsupported stages raise loudly, never acceptance. No runtime integration; dispatch OFF, no activation path or authorization. Synthetic fixtures are not observed market evidence; fixture equality is not live-data equivalence. Resolution-time/not-cycle-frozen and custom-configuration limitations remain; this corpus is not exhaustive.

PAPER ONLY: no production risk/scanner/TA/fill changes, real orders, exchange accounts, API keys, signed endpoints, geo-block, Render/billing, or HFTENGINE changes; no external code copied.

---

## Build 4 part 9 — PARTIAL funded-risk veto corpus (PAPER ONLY)

Browser-only GitHub UI increment; no checkout, shell, Python or local test runs.
CI (ruff + pytest) is the validator, not an assumed pass.

New isolated evaluate_funded_risk extends the early interpreter through captured
slipped entry, take-profit guard, slipped stop, fee-inclusive cost, net stop
proceeds, cost-minus-proceeds risk, and cash guard. These are risk arithmetic,
NOT position sizing. Exact supported AST formula/guard order is pinned; values,
operators and veto reason strings are interpreted from the captured body. Costs
come from the snapshot; proposal/account inputs are caller supplied. No production
decision evaluator is called by the interpreter. The original early evaluator
and all production behavior remain unchanged.

New veto reasons: "Take profit must exceed slipped entry" and
"Insufficient cash; borrowing and leverage forbidden". Complete RiskCheckResult
canonical bytes are compared against unchanged check_trade with unified diffs.
New synthetic Solana n=8: TP below/equal/just-below slipped entry, cash zero/just
below funding edge, TP-before-cash and leverage-before-TP precedence, and TP just
above edge reaching cash rejection. Together with part8 n=6, Solana n=14 dual-path
reject fixtures; Spot n=0; Futures n=0, blocked_http_451/nontradable. Additional
safety tests: three funded boundary/accept fallthroughs fail loudly, two formula/
guard mutations fail closed, and captured cash reason mutation is consumed.

Deferred: per-trade risk, daily loss/projected exposure and drawdown/projected
exposure vetoes after cash; invalid-cost veto remains unsupported (loud failure).
No sizing equivalence, cap exhaustion, minimum-quantity/exchange minimum edges,
notional/concentration/position-count/stale-data paths outside this check_trade
prefix, BUY/WATCH, full accepts, downstream strategy or exits. Passing the cash
guard raises UnsupportedPolicy, never acceptance. Source outside this prefix
is not audited. Synthetic fixtures are not observed market evidence; fixture
byte equality is not live-data equivalence. Resolution-time not cycle-frozen,
custom-config coverage, caller-supplied Spot instruments and unauthenticated
fingerprint limits remain. Dispatch stays OFF, no activation path. No production
risk/scanner/TA/fill changes, exchange accounts/keys/signed endpoints/orders,
geo-block, Render/billing or HFTENGINE changes; no external code copied.

Earlier part8 sections below describe that increment, not part9 coverage.

## Build 4 part 7 — standalone source-backed capture; dispatch OFF

PAPER ONLY. `act.research.semantic_capture.capture_policy` now captures qualified production rule bodies, named assignments and guards (not only hashes), effective settings/bounds/sources including ConcentrationSettings, fees and Binance control values, and explicitly scoped caller-supplied dynamic instrument minimums. No HTTP or checkpoint/runtime behavior was added.

| Market | Standalone capture / resolver test | Complete semantic capture |
|---|---|---|
| Solana v1 | source-backed rules + effective settings/costs; offline full PR #23 resolver test | NOT independently established; Binance minimums not applicable |
| Spot v1 | same plus exact supplied instrument filters; synthetic full-resolver test | NOT independently established; actual instrument scope/values must be supplied |
| Futures v1 | blocked_http_451, no invented instruments/minimums, tradable=false; blocked-state full-resolver test | blocked-state only, NOT a tradable futures capture |

**Partial Priority 1 delivery, not complete semantic-policy capture.** The four-section resolver proves schema/pointer/drift compatibility, not semantic completeness or independent decision-byte equivalence. Source-backed rules remain data, not an executable policy evaluator. Values are resolution-time, not cycle-frozen; custom injected configs/limits are not represented. Resolved Settings constructor/env/dotenv origins cannot be recovered. Fingerprints are shape-checked, not authenticated. Offline synthetic tests are labeled and fixture-bounded, not observed market evidence.

Live paper decisions, legacy champions and existing partial observations remain unchanged. Independent all-market dual-path equivalence, full-snapshot upgrade-on-write migration, persisted-champion dispatch and a real ACT_DISPATCH_MODE=off kill switch remain deferred. Dispatch stays OFF with no activation path added. No risk/scanner/TA/fill changes, exchange accounts/keys/signed endpoints/live orders, geo-block work, Render/billing or HFTENGINE changes. Browser-only environment: CI (ruff + pytest) is the validator; no local runs claimed.

Earlier Build sections below are historical and do not imply deferred work was completed.

### Capture API and limits

`capture_policy(settings, market, *, at, instruments=None, required_symbols=())` returns immutable canonical data and detached `.policy` exports. It does not register, persist or activate the result. For Spot, pass actual public instrument rows with status=available, source, fetched_at, symbols and the exact required-symbol scope. Original LOT_SIZE, optional MARKET_LOT_SIZE and NOTIONAL/MIN_NOTIONAL fields/decimal strings are retained; missing required instruments or minimums fail loudly. Full exchange-universe coverage is not inferred. For blocked Futures supply blocked_http_451 with no symbols; this cannot authorize an instrument.

The rule inventory retains full qualified production definitions plus assignments and guards, preserving formula/default/branch order/veto text. This source inventory is not yet a separately normalized policy evaluator. Resolver tests register all three captured policies as a synthetic all-market registry and invoke the unchanged full PR #23 resolver per market. They test structural acceptance, not independent hardcoded-vs-dispatched equivalence. No legacy identity champion or partial observation is relabeled as complete.

## Build 4 part 6 — PARTIAL observation integrity, dispatch OFF

PAPER ONLY. This browser-only increment does **not** finish Priority 1. Before the
wired checkpoint migration appends any observation child, it now validates the
captured settings values against production models, exact model bounds and source
inventories, risk dataclass inventories/constraints, Solana cost bounds, Binance
fee bounds/provenance, implementation fingerprint shape/inventory, all-market v1
OFF status and explicit limitations. Invalid contents fail loudly before append;
legacy history and champion pointers are not rewritten. Corrected the stale
migration docstring: partial observation migration IS called on checkpoint writes.
Fingerprints are syntax-checked, not authenticated provenance; valid-looking
records are not a semantic completeness certificate.

New offline regression cases damage these nested contents and check rejection
without input mutation, nondefault environment append/idempotence, and checkpoint
failure propagation. These are integrity tests, **not independent decision-path
byte equivalence**. Existing Solana config-roundtrip fixtures remain same-evaluator,
small-n, fixture-bounded and non-exhaustive. Environment values are observations at
resolution time, not guaranteed decision-time or cycle-frozen values. No local
shell/Python or local ruff/pytest was available; GitHub CI is the validator.

Runtime decisions remain hardcoded. **Dispatch stays OFF** because complete named
selection/entry/exit/sizing/cost policies, dynamic per-instrument exchange minimums,
resolver-complete per-market snapshots and the independent all-veto/boundary
Solana/Spot/Futures-limited corpus are still missing. Futures remains
`blocked_http_451`. No persisted-champion dispatcher or production
`ACT_DISPATCH_MODE=off` kill switch is wired; do not rely on that variable.
`ACT_POLICY_DISPATCH=false` only controls the existing standalone prerequisite
helper; true raises DISPATCH_BLOCKED and cannot enable dispatch. It is not a
production kill switch. No activation credential is inferred from green integrity
CI. Snapshot drift/pointer checks in the standalone resolver are not yet runtime
per-market dispatch protection.

Migration today is append-only **partial observation** capture on write. A later
full-snapshot migration must retain event history and original v1 champion IDs,
explicitly associate complete child snapshots with those IDs, verify every market
through the full resolver, and only then wire fail-closed runtime resolution with
independent CI proofs and a real kill switch. Never relabel partial observations
as complete or silently fall back after a dispatch resolution failure.

Deferred: complete semantic snapshots and full migration, independent all-market
byte equivalence, runtime dispatch/kill switch; shadows, attribution/scoreboard,
backtester, OOS and promotion. No risk-limit, scanner/TA, fill-model, exchange
account/key/signed endpoint, live-execution, geo-block, Render/billing or HFTENGINE
changes. No external code copied. No profitability or exhaustive-equivalence claim.

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


## Build 4 part 8 — PARTIAL early-risk interpreter (PAPER ONLY)

Browser-only increment; no local checkout, shell or Python execution.

Delivered: standalone `act.research.early_risk.evaluate_early_risk` consumes a CapturedPolicy, parses the captured `act.risk.limits.check_trade` body with AST (no eval/exec), checks the ordered three supported guards and interprets their operands/operators and captured veto strings. It validates captured Solana fee_pct/slippage_pct as finite [0,100) inputs, requires dispatch off, and returns the complete RiskCheckResult mapping ONLY for early rejects. It does not call production decision functions. Unsupported cases raise UnsupportedPolicy, never acceptance or an invented partial decision.

Synthetic offline corpus: Solana n=6 (leverage=2; leverage=1.0000001; missing stop; stop=entry; stop just above entry; leverage+missing-stop precedence). Three veto reasons: No leverage permitted; A stop-loss invalidation point is required; Long stop-loss must be below the quoted entry price. Tests compare all canonical RiskCheckResult bytes against unchanged production check_trade on the same proposal, with separate live Settings/RiskLimits/account objects on the production side and captured JSON on the interpreter side. A mismatch reports a unified byte-text diff and fails. Three additional tests cover unimplemented acceptance, guard drift rejection and captured reason mutation. CI is the validator; this text does not assert a local or CI pass.

Deferred: full candidate/market-context strategy decisions; BUY/REJECT/WATCH outputs; sizing, entry/exit parameters; all remaining vetoes; cap exhaustion; exchange minimums; sizing variations; Spot n=0; Futures n=0 (still blocked_http_451/nontradable, no invented instruments). This is NOT the requested all-market corpus and does NOT establish full Solana equivalence. Only the three captured guards/early return messages above are interpreted; all other rule bodies, resolved models, risk limits, controls, instrument minimums, exit/fill/cost arithmetic are ignored, not claimed equivalent. Changes outside the supported source slice are not independently audited. Synthetic fixtures are not observed market evidence, and fixture equivalence is not live-data equivalence. Existing resolution-time/not-cycle-frozen, custom-config, caller-supplied Spot scope, coverage and unauthenticated fingerprint limitations remain. No runtime integration, persisted-champion dispatcher or ACT_DISPATCH_MODE switch is added. Dispatch remains OFF.

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
