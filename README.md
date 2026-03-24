# Artha

Artha is a read-only portfolio research and rebalancing agent for Indian equity portfolios. It uses Anthropic for reasoning, Zerodha/Kite via MCP for live holdings data, Tavily for web research, and local markdown skills for portfolio-specific prompt guidance.

The repository exposes three operator surfaces:

- a CLI in [`main.py`](main.py)
- a FastAPI backend in [`api/main.py`](api/main.py)
- an MCP server in [`mcp_server.py`](mcp_server.py)

Artha is analysis-only. It does not place orders or execute trades.

## What it does

- syncs live equity holdings, MF holdings, cash, and profile data from Kite
- persists local snapshots under `data/kite/`
- runs per-holding analyst research for equity names while excluding passive instruments from rebalance actions
- computes deterministic rebalance math in Python
- synthesizes a final `PortfolioReport`
- saves reports, manifests, and LLM usage ledgers under `reports/`

MF holdings are persisted and surfaced, but they are not included in equity rebalancing math.

## Prerequisites

- Python 3.11+
- `ANTHROPIC_API_KEY`
- Kite MCP access through either `KITE_MCP_URL` or a local `KITE_MCP_COMMAND`

For full analyst and research workflows, you also need:

- `TAVILY_API_KEY`

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Minimum `.env` values:

```env
ANTHROPIC_API_KEY=
MODEL=claude-sonnet-4-6
KITE_MCP_URL=https://mcp.kite.trade/mcp
```

Common optional runtime settings:

```env
TAVILY_API_KEY=
ANALYST_MODEL=claude-haiku-4-5
ANALYST_MAX_TOKENS=4096
ANALYST_MAX_SEARCHES=4
ANALYST_PARALLELISM=2
ANALYST_MIN_START_INTERVAL_SECONDS=3
SUMMARY_MAX_TOKENS=700
COMPANY_ANALYSIS_MAX_AGE_DAYS=7
COMPANY_CACHE_PRICE_MOVE_THRESHOLD_PCT=15
MAX_TOKENS=8096
MAX_ITERATIONS=10
TRANSIENT_RETRY_ATTEMPTS=3
TRANSIENT_RETRY_BASE_DELAY_SECONDS=1.0
REPORTS_DIR=./reports
LLM_USAGE_DIR=./reports/usage
LOG_LEVEL=INFO
KITE_MCP_COMMAND=
KITE_MCP_ARGS=[]
KITE_MCP_ENV_JSON={}
KITE_MCP_TIMEOUT_SECONDS=30
KITE_DATA_DIR=./data/kite
KITE_LOGIN_TIMEOUT_SECONDS=180
KITE_LOGIN_POLL_INTERVAL_SECONDS=3
```

Optional provider runtimes:

```env
YFINANCE_MCP_COMMAND=uvx
YFINANCE_MCP_ARGS=["--from","git+https://github.com/richin13/yahoo-finance-mcp@f54e92663d23282fef913f47f6b1bd603e861cbb","yahoo-finance-mcp"]
YFINANCE_MCP_ENV_JSON={}
YFINANCE_MCP_TIMEOUT_SECONDS=30

NSE_MCP_COMMAND=npx
NSE_MCP_ARGS=["stock-nse-india@1.3.0","mcp"]
NSE_MCP_ENV_JSON={"NODE_ENV":"production"}
NSE_MCP_TIMEOUT_SECONDS=30

MOSPI_MCP_URL=https://mcp.mospi.gov.in/
MOSPI_MCP_TIMEOUT_SECONDS=30

NSE_BSE_MCP_URL=
NSE_BSE_MCP_TIMEOUT_SECONDS=30
```

Optional observability settings:

```env
TELEMETRY_SERVICE_NAME=artha
TELEMETRY_ENVIRONMENT=development
TELEMETRY_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_EXPORTER_OTLP_HEADERS={}
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

## CLI usage

```bash
.venv/bin/python main.py kite-login
.venv/bin/python main.py kite-sync
.venv/bin/python main.py holdings
.venv/bin/python main.py rebalance
.venv/bin/python main.py run
.venv/bin/python main.py run --ticker KPITTECH
.venv/bin/python main.py run --rebalance-only
.venv/bin/python main.py analyst --ticker BSE
.venv/bin/python main.py compare-providers --ticker BSE
.venv/bin/python main.py research
.venv/bin/python main.py usage-report --last 10
```

Command notes:

- `kite-login`: starts Kite login, waits on the same MCP session, and saves fresh equity and MF snapshots
- `kite-sync`: fetches and saves fresh equity and MF snapshots
- `holdings`: prints the live equity holdings table
- `rebalance`: computes a math-only rebalance report from the latest saved local equity snapshot
- `run`: full portfolio flow; reuses same-day saved Kite snapshots when available, otherwise performs a fresh sync
- `run --ticker KPITTECH`: runs a single-stock analysis and emits a one-stock `PortfolioReport`
- `run --rebalance-only`: performs a fresh sync and returns drift-only rebalance actions without fundamental analysis
- `analyst --ticker BSE`: standalone analyst path without full portfolio orchestration
- `compare-providers --ticker BSE`: writes Yahoo Finance and NSE India payloads to `data/kite/provider_compare/`
- `research`: runs deep web research on the latest saved equity and MF snapshots and writes a digest under `reports/research/`
- `usage-report --last 10`: prints recent historical run summaries from the usage ledger

## API usage

Start the API:

```bash
.venv/bin/python -m uvicorn api.main:app --reload --port 8000
```

Endpoints:

- `GET /api/health`
- `GET /api/holdings`
- `GET /api/mf-holdings`
- `GET /api/reports`
- `GET /api/reports/latest`
- `GET /api/reports/{report_id}`
- `GET /api/price-history/{ticker}`
- `POST /api/run`

Notes:

- `GET /api/holdings` prefers live Kite data and falls back to the latest saved portfolio snapshot if live access fails
- `POST /api/run` streams structured SSE progress events

## MCP server usage

Run the MCP server:

```bash
.venv/bin/python mcp_server.py
```

The MCP surface is read-only. It exposes:

- report listing and retrieval
- cached analyst artifact listing and retrieval
- a tool to trigger Artha analysis runs without adding any order-placement capability

## Runtime flow

Full `run` flow:

1. Load settings from `.env`
2. Reuse same-day saved Kite snapshots when available, otherwise sync fresh data from Kite
3. Persist fresh portfolio and MF snapshots under `data/kite/`
4. Build shared prompt context, macro context, and price context
5. Reuse fresh company artifacts from `data/kite/companies/` where possible
6. Refresh stale or missing company artifacts with analyst sub-agents
7. Compute deterministic rebalancing actions in Python
8. Build the final portfolio summary and validate it as `PortfolioReport`
9. Save the report, index entry, manifest, and usage records

If final parsing fails, Artha falls back to a rebalance-oriented valid report rather than crashing.

## Data layout

- `skills/`: prompt source material
- `data/kite/auth/`: Kite login artifacts
- `data/kite/portfolio/`: equity snapshots
- `data/kite/mf/`: mutual fund snapshots
- `data/kite/companies/`: per-company analysis artifacts
- `data/kite/provider_compare/`: side-by-side provider payload exports
- `reports/`: saved portfolio reports plus `index.json`
- `reports/manifests/`: per-run manifests
- `reports/research/`: deep-research artifacts and combined digests
- `reports/usage/`: LLM usage ledgers and run summaries

## Architecture

Primary code locations:

- [`main.py`](main.py): CLI entrypoint
- [`mcp_server.py`](mcp_server.py): MCP entrypoint
- [`application/orchestrator.py`](application/orchestrator.py): full-run orchestration
- [`application/agent.py`](application/agent.py): Sonnet-driven report synthesis and final parsing
- [`application/research_orchestrator.py`](application/research_orchestrator.py): holding-level deep research fan-out
- [`analysis/`](analysis): analyst, artifact, and verification logic
- [`kite/`](kite): Kite session, sync, and tool adapters
- [`providers/`](providers): external provider integrations
- [`persistence/store.py`](persistence/store.py): snapshots, reports, manifests, and artifacts
- [`rebalance.py`](rebalance.py): deterministic drift and action calculation
- [`models/`](models): Pydantic schemas

## Tests

Run the full suite:

```bash
.venv/bin/python -m pytest
```

Focused runs:

```bash
.venv/bin/python -m pytest tests/test_agent.py
.venv/bin/python -m pytest tests/test_tools.py
.venv/bin/python -m pytest tests/test_rebalance.py
.venv/bin/python -m pytest tests/test_mcp_server.py
.venv/bin/python -m pytest tests/test_api_run.py
```

## Repository automation

The repository already includes:

- `.github/workflows/ci.yml`: ruff plus pytest with `--cov-fail-under=80`
- `.github/workflows/codeql.yml`: CodeQL for Python on pull requests, pushes to `main`, and a weekly schedule
- `.github/dependabot.yml`: weekly grouped updates for Python dependencies and GitHub Actions
- `.github/copilot-instructions.md`: repository-wide Copilot guidance
- `.github/instructions/*.instructions.md`: path-specific guidance

Recommended GitHub settings outside the repo:

- require `CI` and `CodeQL` before merge
- require at least one human approval before merge
- enable secret scanning and push protection

## Safety

Artha is built for analysis, not execution. Review any recommendation manually before acting, and do not add trade placement or auto-trading behavior unless that change is explicitly requested and governed.
