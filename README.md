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


## Binance public-data origins (PAPER ONLY)

Render returned HTTP 451 from both production exchangeInfo endpoints. GeoBlocked
and the existing error taxonomy remain unchanged: no data means no Binance scan
or paper trade. There is no automatic fallback or substitute exchange.

| Environment variable | Exact default | Accepted origins |
| --- | --- | --- |
| BINANCE_SPOT_BASE_URL | https://api.binance.com | default or https://data-api.binance.vision |
| BINANCE_FUTURES_BASE_URL | https://fapi.binance.com | default only |

Unset preserves production behavior. Use an exact bare HTTPS origin without a
trailing slash. Empty values, unofficial hosts, credentials, ports, paths, queries
and fragments fail closed with ValueError before fetching. Clients record the
validated base_url; HTTP response provenance records base_url and the actual
source_url, including error responses and retries, for status/dashboard provenance.

Binance's [Market Data Only URLs](https://developers.binance.com/en/docs/products/spot/faqs/market_data_only)
lists exchangeInfo, ticker/24hr, klines and depth on data-api.binance.vision.
This is official keyless Binance Spot market data, not another exchange, proxy
or third-party mirror. The [Spot general documentation](https://developers.binance.com/en/docs/products/spot/rest-api)
recommends that domain for public-only APIs. The
[USDT-M Futures general information](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)
lists fapi.binance.com; no equivalent public-only Futures domain is documented
there. The Spot vision origin is therefore rejected for Futures.

Those documents do not promise US-IP or Render-region accessibility. Configuring
the origin does not prove reachability: the real deploy-region test happens after
merge and separate owner approval to set the Render environment variable.
Futures may remain blocked. This PR makes no Render changes, introduces no keys,
signed endpoints or order placement, and changes no strategies. PAPER ONLY.
