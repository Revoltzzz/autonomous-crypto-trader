# Binance scanner selection module

### Spot mover-priority selection (PAPER ONLY)

Spot reuses the full-market 24-hour ticker fetched in this cycle. Positive
priceChangePercent values rank descending as gainers; negative values rank
ascending as losers; ties use symbol ascending and zero is neither. Only current
TRADING, Spot-allowed exchangeInfo symbols qualify. Set
BINANCE_SPOT_MOVER_TOP_N (integer 0..10, default 1; 0 disables mover priority).
Selection interleaves gainer rank 1, loser rank 1, then rank 2, etc., up to N per
side; the existing least-recently-completed rotation fills the remaining budget.
Rotation indices are zero-based positions in that cycle's full rotation order.
Futures selection is unchanged.

Ranking adds ZERO API calls or request weight: it changes WHICH symbols receive
the existing depth/1m requests, not how many. Fetch/time limits, payload caps,
retry accounting and the conservative weight guard remain unchanged. The default
six-fetch budget normally permits at most two Spot scans (100 bulk weight plus
14 for two scans), and can permit fewer or none after failures/retries. Ranking
the entire eligible ticker universe is not scanning it. Large N can crowd out
rotation; mover-chasing and repeated-selection bias are explicit research risks,
not an advantage or profit claim. Report small-n results as such.

A missing, malformed, duplicate or incomplete eligible ticker set is NEVER ranked,
and no prior ticker snapshot is reused. The scanner logs
SPOT_MOVER_ROTATION_FALLBACK and records a data gap, discards all ticker metrics,
and attempts current-exchangeInfo rotation only within the remaining budgets.
Change/volume/suitability score are null on fallback, never synthetic zeros.
If exchangeInfo is unavailable, or budgets/access prevent further scans, coverage
can remain zero; no fabricated universe or completed work is reported.

Every completed symbol records selection basis (top_gainer/top_loser with rank
and priceChangePercent, or rotation with index) plus the ranking snapshot SHA256.
Failed symbol attempts retain the same selection provenance in errors. The
canonical, detached JSON ranking snapshot carries cycle ID, timestamp, complete
positive/negative rankings, top-N, current ticker provenance and any gap. It lives
in that cycle's existing persisted report, never in the rolling scanner checkpoint;
new cycles append separate reports rather than editing old rankings. Downstream
agents see the existing recursively frozen snapshot. The hash detects accidental
payload changes; this is not a new tamper-proof database or retention guarantee.
Existing single-worker/cycle-ID-idempotent persistence and its failure reporting
still apply. HUD summaries read this stored snapshot and recorded scan counts,
not a new fetch; escaping, wrapping, PAPER MODE / LAST STORED STATE remain.

**Losers are RESEARCH / BOUNCE-WATCH ONLY. Spot is LONG-ONLY: NO SHORTING.**
Negative-change candidates (including rotation picks) carry that label through
the existing scanner-to-paper candidate merge. A top gainer earns no automatic
approval. Manipulation heuristics, full TA corroboration and all risk-agent
vetoes apply unchanged. No changes to TA, risk limits, execution, Solana, futures,
news or strategy/research registry behavior. See
the root README scanner section for the surrounding pipeline.


## Implementation and offline validation

`movers.py` is a pure, I/O-free selection/snapshot helper. `scanner.py` owns public
data access and existing budget admission. `market_reporting.py` reads stored
evidence only. `tests/test_spot_movers.py` exercises synthetic offline fixtures;
these fixtures are not observed prices or evidence of profitability. The existing
rotation test explicitly sets top-N=0 to isolate its original checkpoint behavior.
CI (ruff + pytest) is the validator; no local test run is claimed.
