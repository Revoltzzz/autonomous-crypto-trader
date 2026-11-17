# LIVE TRADING DESIGN — DESIGN ONLY / NOT ACTIVATED

Nothing in this document is implemented, activated or deployed. Build3 remains
PAPER ONLY. This is not permission to connect an exchange or execute an order.

## Boundary
A future executor could accept immutable risk-approved intents (market, symbol,
side, quantity, decision timestamp, expiry, risk-limit version, idempotency key)
and return acknowledgements, fills, fees and explicit rejections with provenance.
The paper executor must never import a live adapter. A separate executable and
separate deployment/account boundary would be mandatory, not an environment flag
that turns this paper app live. No live implementation or order endpoint is added.

## Credential isolation
Keys must never enter the paper system, its environment, database, logs, reports,
agent prompts or browser UI. Future credentials would belong only to a separate
isolated executor secret store, with least privilege, no withdrawals, IP controls,
rotation and revocation. Paper agents would have no credential access whatsoever.
A future design review must address authentication, replay protection and audit
integrity; no credentials should be provisioned for the present application.

## Maximum experimental account: $100
A hypothetical separately approved live experiment would be capped at $100 total
account equity, no leverage, no borrowing, no automated top-ups and no recovery
sizing. Conservative indicative limits: risk<=0.25% equity per trade, one open
position, gross exposure<=10%, daily realized+unrealized loss<=1% and cumulative
loss<=3%. If exchange minimums make this infeasible, do not trade; never increase
risk or capital to bypass a minimum. These are proposed upper bounds, not active
settings or an authorization to fund an account.

## Kill switches and operational controls
Independent operator and executor kill switches must block new entries before
network submission. Trigger on stale/incomplete data, clock skew, reconciliation
mismatch, duplicate identifiers, unknown order state, loss/exposure limits,
credential errors and dependency failures. Unknown outcome must never cause an
automatic resubmission. Reconcile exchange positions and balances before restart;
require explicit human review to clear critical halts. Cancellation and safe
risk-reducing close procedures need separate reviewed policies and bounded cost.
Exchange-native protective orders and outage behavior require testing before any
live proposal; simulated stops offer no actual protection.

## Preconditions, not roadmap activation
Independent security review, audited accounting, realistic out-of-sample evidence
with n, slippage/fee/funding validation, offline fault injection, exchange sandbox
validation and an explicit owner approval of code, capital and deployment would
all be prerequisites. Existing readiness gates alone cannot authorize live trading.
No live feature flag, credentials, client, signed endpoint or order path exists in
Build3. Nothing in this document has been activated or deployed.
