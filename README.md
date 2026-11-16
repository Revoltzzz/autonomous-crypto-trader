null

## Agent team build 1: read-only news research

Agents live in `src/act/agents/`. `Agent` defines `name`, `describe()`, and async
`run(context) -> dict`; `Registry` rejects duplicate names. The cycle runs registered
agents **after** all existing strategy/position/learning decisions. No news output
is a strategy input. No exchange execution, trading credentials, paid API, API key,
sentiment model, new dependency or real-money capability is added.

Each agent receives a recursively frozen deep snapshot of the cycle report, not
a mutable ledger. All agent network reads must use `context.fetch(https_url)`,
which counts failed attempts too and records source URL, observation timestamp,
HTTP status and outcome. The async runner enforces per-agent elapsed time with
`asyncio.wait_for`; agents must cooperate with cancellation (this is not a sandbox
for arbitrary CPU-bound or cancellation-suppressing plugins). HTTP calls have a
maximum four-second timeout, no redirects and no automatic retries. Responses
above 2 MB and XML DTD/entities are rejected. Exceptions are logged with full
tracebacks and recorded as failed results; another agent and the trading cycle
continue. Cancellation during shutdown propagates.

Configuration (environment only; no secrets in source):

| Variable | Default / meaning |
| --- | --- |
| `AGENT_TIME_BUDGET_SECONDS` | 12 seconds per agent; 0 disables work; hard maximum 60 |
| `AGENT_FETCH_BUDGET` | 6 HTTP attempts per agent; 0 makes no requests; hard maximum 20 |
| `AGENT_NEWS_TIME_BUDGET_SECONDS` | Optional per-news override |
| `AGENT_NEWS_FETCH_BUDGET` | Optional per-news override (integer) |
| `TELEGRAM_BOT_TOKEN` | Unset: inert, one disabled log per process. Set: enables brief push |
| `TELEGRAM_CHAT_ID` | Required when token is set; never hardcoded |

Other registered agents use `AGENT_<UPPERCASE_NAME>_TIME_BUDGET_SECONDS` and
`AGENT_<UPPERCASE_NAME>_FETCH_BUDGET` overrides. Invalid budgets fail that agent
loudly rather than silently choosing different limits.

### Free sources and verification

The configured public RSS/Atom sources are
[CoinDesk](https://www.coindesk.com/arc/outboundfeeds/rss/),
[Cointelegraph](https://cointelegraph.com/rss),
[The Block](https://www.theblock.co/rss.xml),
[Decrypt](https://decrypt.co/feed),
[Coinbase official blog](https://www.coinbase.com/blog/rss.xml), and
[Federal Reserve official releases](https://www.federalreserve.gov/feeds/press_all.xml).
These are source configurations, not a guarantee of current feed availability;
HTTP/parse failures remain errors, not invented headlines. Feeds have a process-local
60-second minimum cooldown; 429 Retry-After (seconds or HTTP date) extends it,
capped at a day. No retries in the same cycle. Feed order is fixed; reducing the
fetch budget may omit later sources, which are recorded as skipped.

Items preserve the reported title (no generated summaries), publication time when
parseable, article/feed URLs, outlet/editorial group and fetch time. URL fragments
and UTM query parameters are ignored for URL deduplication; titles with at least
six words and >=0.92 similarity are grouped, keeping related headlines and sources.
**Fuzzy grouping never verifies a claim.** The label `verified` requires either:

* a headline from a configured primary feed linking to that official domain, or
* the exact normalized substantive headline (at least six words), published within
  24 hours at two different article URLs and configured editorial groups, with
  neither item marked by known syndication/press-release strings.

This is conservative automatic corroboration, **not a fact-checking guarantee**.
Editorial-group identity and syndication markers are imperfect proxies; unrelated
wording is deliberately left unverified even if it might describe the same event.
A near-duplicate primary headline cannot confirm another representative headline.
Unverified items remain visible; missing dates cannot satisfy the two-outlet rule.
Labels include their rule/reason and exact verification URLs for audit.

Classification is deterministic keyword matching: regulatory, macro, exchange,
solana-ecosystem, then token-specific fallback (not inferred token identity). The
1–4 relevance points are scope priorities with recorded keywords/reasons, **not
sentiment, probability, price forecasts or trade recommendations**.

### Persistence, HUD, digest and optional Telegram

Full results/provenance are stored under `agents` and the digest-ready summary
under `news_brief` in the existing cycle report, using the unchanged Supabase
`persist_paper_cycle` RPC JSON payload (or the existing local report backend).
No migration is needed. `GET /status` exposes the full latest report, including
`news_brief` with as-of time, total/verified/unverified counts and up to five
verified items; before any report it returns an honest waiting brief. The HUD
shows escaped headlines, labels/reasons, source links, relevance and as-of time.
Empty/unavailable snapshots do not reuse headlines or fabricate data.

When explicitly configured with both Telegram variables, each cycle attempts one
plain-text brief push with the top verified items only, a bounded message and
timeout, no automatic retry. Missing token makes no request, even with a chat ID.
Missing chat ID or delivery failures are recorded/logged without crashing the
cycle. Tokens and request URLs containing them are never logged by this module;
transport library logging is suppressed during the push. No token exists by
default. Render free-tier sleep still applies: this adds no keep-alive or paid
service, and the existing roughly-20-minute awake scheduler is unchanged.

Run `ruff check .` and `pytest` (offline). `tests/test_agents_news.py` covers
registry validation, immutable snapshots, time/fetch budgets and isolation,
RSS/Atom parsing, dedupe vs verification, classification/relevance, source errors,
cooldowns, escaped empty/news panels, and Telegram disabled/safe-delivery gates.
