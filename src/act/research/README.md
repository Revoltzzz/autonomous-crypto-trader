## Build 4 part 11 — PARTIAL Solana sizing-region equivalence (PAPER ONLY)

This section supersedes older deferred-sizing statements below; older sections are historical increments.
Browser-only GitHub UI, no checkout/shell/Python and no local test runs. CI (ruff + pytest) is the validator.

The isolated evaluate_sizing consumes the captured act.strategy.evaluate slipped-entry assignment and contiguous six-assignment sizing region: equity times captured 0.01 risk fraction; nominal stop-distance notional; fee/slippage-inclusive entry cost and net stop proceeds; equity times captured RiskSettings.max_position_notional_fraction executed-notional cap; minimum of nominal, funded-risk and cap quantities, followed by the captured (1 - 1e-10) haircut. It consumes captured fee_pct/slippage_pct values (converted to fractions), model cap fraction, rule bodies and explicit not-applicable Solana instrument-minimums marker. Exact AST topology/formulas are pinned; drift or missing bodies/values fails loudly. Entry/stop and equity are caller-supplied upstream state, NOT frozen market observations.

Sizing corpus: Solana n=13 synthetic six-field canonical-byte comparisons against locals observed from the UNCHANGED production evaluate call (test-only return profiler, restored in finally). Complete sizing-region mapping is risk_budget, nominal_notional, entry_cost, stop_proceeds, notional_cap, size. No float rounding, approximate assertions, field deletion or production arithmetic calls in the interpreter; unified diff on byte mismatch. Tests additionally assert production proposal size equals the interpreted size. This is NOT complete DecisionRecord/proposal-byte or upstream stop-construction equivalence. Cases cover binding cap, nominal/risk tie, fee-inclusive binding risk, slippage and both costs, tiny positive quantity, nextafter entry/stop neighbors and actual computed float cap-switch threshold plus its neighbors. The 0.01 and 1e-10 production constants are retained exactly as Python floats; no approximate decimal boundary is substituted. Haircut is pinned, not exchange-step rounding.

Solana has NO exchange lot/minimum-quantity/min-notional gate or quantity-step rounding in this captured sizing region; instrument minimums are explicitly not_applicable, not a missing exchange observation. Minimum-quantity edges are therefore N/A, not invented. Zero equity/cap exhaustion is outside the positive-sizing subset and fails loudly, rather than manufacturing an accepted zero-size trade. Existing n=35 Solana veto comparisons remain separate (early/funded/remaining); new sizing n=13, combined comparison count n=48. Spot veto/sizing n=0: deferred, only caller-supplied instrument scope/values, synthetic fixtures would not be observed exchange evidence. Futures veto/sizing n=0, blocked_http_451/nontradable; no instruments invented.

Safety tests cover sizing formula/min/haircut/slipped-entry drift, wrong market, dispatch ON, missing capture body/value, unexpected minimums, zero equity, nonfinite input, stop equal to entry and missing stop. No full strategy acceptance, screening, profile-stop construction, post-gate reporting, executor sizing/rounding or exits are interpreted. Invalid-cost veto remains loud unsupported in the existing interpreters and deferred here. No required sizing-region body/value capture gap was found by source inspection; missing captures are explicit CAPTURE_GAP failures, not approximations. Resolution-time NOT cycle-frozen, custom config and unauthenticated fingerprint limitations remain. Small-n synthetic fixture equality is NOT live-data equivalence, observed market evidence, exhaustive proof or profitability evidence.

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
