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
