# Artha

Artha is a read-only portfolio research and rebalancing agent for Indian equity portfolios. It uses Anthropic for reasoning, Tavily for controllable web research snippets, connects to Zerodha/Kite through an MCP server configured in `.env`, caches strict JSON company-analysis artifacts under `data/kite/companies/`, refreshes them only when stale, uses Claude Haiku for cost-sensitive company artifact generation, and keeps the main Artha synthesis path on Claude Sonnet.

The repository now also exposes a FastAPI layer under `api/` for dashboard use cases. The companion Next.js frontend lives in the sibling folder `../artha-ui`.

## Prerequisites

- Python 3.11+
- Anthropic API key
- Tavily API key
- Access to Zerodha’s hosted Kite MCP or a working Kite MCP command you can run from this machine

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Set the API keys in `.env`:

```bash
ANTHROPIC_API_KEY=
TAVILY_API_KEY=
```

Set the Kite runtime in `.env`:

```bash
KITE_MCP_URL=https://mcp.kite.trade/mcp
KITE_MCP_COMMAND=
KITE_MCP_ARGS=[]
KITE_MCP_ENV_JSON={}
KITE_MCP_TIMEOUT_SECONDS=30
KITE_DATA_DIR=./data/kite
```

Optional Yahoo Finance MCP runtime for analyst enrichment:

```bash
YFINANCE_MCP_COMMAND=uvx
YFINANCE_MCP_ARGS=["--from","git+https://github.com/richin13/yahoo-finance-mcp","yahoo-finance-mcp"]
YFINANCE_MCP_ENV_JSON={}
YFINANCE_MCP_TIMEOUT_SECONDS=30
```

Optional NSE India provider runtime for side-by-side market-data comparison:

```bash
NSE_MCP_COMMAND=npx
NSE_MCP_ARGS=["stock-nse-india","mcp"]
NSE_MCP_ENV_JSON={"NODE_ENV":"production"}
NSE_MCP_TIMEOUT_SECONDS=30
```

Model routing in `.env`:

```bash
MODEL=claude-sonnet-4-6
ANALYST_MODEL=claude-haiku-4-5
ANALYST_MAX_TOKENS=2500
ANALYST_MAX_SEARCHES=3
ANALYST_PARALLELISM=2
ANALYST_MIN_START_INTERVAL_SECONDS=3
HAIKU_INPUT_TPM=50000
HAIKU_OUTPUT_TPM=10000
SUMMARY_MAX_TOKENS=700
COMPANY_ANALYSIS_MAX_AGE_DAYS=7
TRANSIENT_RETRY_ATTEMPTS=3
TRANSIENT_RETRY_BASE_DELAY_SECONDS=1.0
LLM_USAGE_DIR=./reports/usage
TELEMETRY_SERVICE_NAME=artha
TELEMETRY_ENVIRONMENT=development
TELEMETRY_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_EXPORTER_OTLP_HEADERS={}
# Optional: get keys at langfuse.com
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

Authenticate and sync fresh snapshots:

```bash
.venv/bin/python main.py kite-login
.venv/bin/python main.py kite-sync
```

`kite-login` is the supported auth flow. It keeps the same MCP session alive while you complete browser login, then fetches and stores fresh equity and MF snapshots once authentication succeeds. `kite-sync` is the same sync primitive without a separate report step.

Start the API server:

```bash
.venv/bin/python -m uvicorn api.main:app --reload --port 8000
```

## Usage

```bash
.venv/bin/python main.py kite-login
.venv/bin/python main.py kite-sync
.venv/bin/python main.py rebalance
.venv/bin/python main.py run
.venv/bin/python main.py run --ticker KPITTECH
.venv/bin/python main.py run --rebalance-only
.venv/bin/python main.py research
.venv/bin/python main.py holdings
.venv/bin/python main.py analyst --ticker BSE
.venv/bin/python main.py compare-providers --ticker BSE
.venv/bin/python main.py usage-report --last 10
```

Supported flows:

- `kite-login`: authenticate a live hosted Kite MCP session in the browser
- `kite-sync`: fetch fresh equity and MF snapshots and persist them locally
- `rebalance`: generate a math-only rebalancing report from the latest saved local equity snapshot, with no LLM call
- `run`: reuses today's saved Kite equity and MF snapshots if they already exist locally; otherwise it performs one fresh Kite sync for the day, persists the snapshots locally, reuses fresh company-analysis artifacts from `data/kite/companies/` where possible, refreshes stale or missing company analysis on Claude Haiku, converts artifacts into rebalancing verdicts, and synthesizes a final portfolio report on Claude Sonnet. If the full run still fails after bounded transient retries, it aborts immediately and writes a structured failure log.
- company analysis now includes a compact Yahoo Finance snapshot and one shared MoSPI macro summary per run; both are best-effort enrichments and never enable trade execution
- `run --ticker KPITTECH`: runs the same cache-backed company-analysis pipeline Artha uses, then emits a one-stock `PortfolioReport` and saves/refreshes `data/kite/companies/KPITTECH.json` as needed
- `run --rebalance-only`: checks Kite session, fetches fresh snapshots, and computes equity-only rebalancing actions
- `research`: reads the latest saved equity and MF snapshots, runs one deep-research sub-agent per holding with Tavily-backed `tavily_search`, saves one file per holding, and writes a combined digest
- `holdings`: checks Kite session, fetches fresh snapshots, and prints the latest equity holdings table
- `analyst --ticker BSE`: runs the standalone Yahoo Finance-backed analyst path only, without Kite sync or portfolio synthesis
- `compare-providers --ticker BSE`: fetches provider-specific data from Yahoo Finance and NSE India and writes two separate JSON files under `data/kite/provider_compare/`
- `usage-report --last 10`: prints recent historical run summaries from the persistent run ledger

Dashboard API endpoints:

- `GET /api/health`: health check
- `GET /api/holdings`: live equity holdings plus latest saved MF snapshot; if live Kite access fails but a saved portfolio snapshot exists, the API returns the cached holdings with `live_status="fallback"` and an optional reconnect URL
- `GET /api/reports`: report index with verdict counts and error counts
- `GET /api/reports/latest`: latest full `PortfolioReport`
- `GET /api/reports/{report_id}`: full saved report by filename stem
- `GET /api/price-history/{ticker}`: 1Y daily candles for a holding found in the latest report
- `POST /api/run`: streamed SSE wrapper around `python main.py run`

LLM cost logging:

- `run`, `run --ticker`, and `research` now append one JSON object per Anthropic call under `reports/usage/llm_usage_YYYYMMDD.jsonl`
- each entry records run id, command, label, model, input/output tokens, cache tokens, web-search count, and estimated USD cost
- every run also appends one summary row to `reports/usage/run_summaries.jsonl` with total cost, total calls, phase/model breakdowns, and the daily usage log path
- failed full runs also append one row to `reports/usage/run_errors.jsonl` with failed phase, ticker when available, retry count used, and error details
- the CLI prints a per-run estimated LLM cost summary and the JSONL path after completion
- before each analyst and portfolio-summary Anthropic call, Artha tries Anthropic's exact zero-cost token counter first and falls back to a rough estimate if the counter is unavailable

Observability and tracing:

- set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and optionally `LANGFUSE_BASE_URL` to export OpenTelemetry traces directly to Langfuse
- or set `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS` to ship traces to any OTLP-compatible backend
- Artha emits one root span per run and one child span per Anthropic call, including model, tokens, web-search count, and estimated cost attributes

## Architecture

`run` now uses a verdict-driven orchestrator:

1. Sync live equity holdings, MF holdings, cash, and profile from Kite
   If today's snapshots already exist locally, reuse them and skip a fresh hosted Kite login/sync
2. Exclude `LIQUIDBEES`, `NIFTYBEES`, `GOLDCASE`, and `SILVERCASE` from analyst fan-out while still keeping them in portfolio totals
3. Fetch one compact price-history summary once per analyzable equity holding: `52w_high`, `52w_low`, `current_vs_52w_high_pct`, `price_1y_ago`, `price_change_1y_pct`
4. Fetch one shared best-effort macro context from MoSPI for CPI, IIP, and GDP growth and inject the compact summary into every analyst call
5. Check `data/kite/companies/{ticker}.json` first for each analyzable holding and reuse it if the artifact is valid and no older than `COMPANY_ANALYSIS_MAX_AGE_DAYS`
6. Refresh only missing, invalid, or stale company artifacts with Claude Haiku and Tavily-backed `tavily_search`, using Instructor-enforced structured output plus compact analyst input payloads, Yahoo Finance snapshot enrichment, staggered starts, and a sliding token budget to stay within provider TPM limits
7. Convert each cached or refreshed company artifact into a normalized Artha verdict
8. Merge analyst verdicts with deterministic drift math to produce final action fields
9. Run one short no-tool synthesis call on Claude Sonnet for the final portfolio summary

MF holdings are saved and surfaced informationally, but they are never analyzed as stocks and never included in equity rebalancing math.

## Output Contract

Portfolio runs and `run --ticker` persist `PortfolioReport` JSON with:

- `portfolio_snapshot`
- `verdicts`
- `portfolio_summary`
- `total_buy_required`
- `total_sell_required`
- `errors`

Each verdict is a `StockVerdict` with:

- `verdict`: `STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL`
- `confidence`: `HIGH | MEDIUM | LOW`
- `thesis_intact`, bull/bear cases, watch item, red flags
- market fields: current price, buy price, P&L%
- optional `yfinance_data` for flat market-metric enrichment
- final rebalance action, rupee sizing, and reasoning
- source URLs from the saved analyst report card, duration, and optional error

The CLI prints:

- a portfolio snapshot summary
- an analyst verdict table
- total buy/sell requirements
- a short portfolio summary
- completion timing and analyst/error counts

Company artifact cache:

- `data/kite/companies/{ticker}.json` stores strict JSON with metadata and a validated analyst report card
- artifacts are reused for up to `COMPANY_ANALYSIS_MAX_AGE_DAYS` days
- legacy `high_52w` / `low_52w` company artifacts are auto-migrated to canonical `52w_high` / `52w_low` keys on load
- old Python-code-style analyst payloads are not reused; they are treated as invalid cache and refreshed
- `reports/usage/llm_usage_YYYYMMDD.jsonl` stores the per-call Anthropic usage ledger used for cost analysis
- `reports/usage/run_summaries.jsonl` stores one persistent cross-run summary row per portfolio, research, or ticker run

Data layout:

- `data/kite/auth/`: login artifacts
- `data/kite/portfolio/`: latest and historical equity snapshots
- `data/kite/mf/`: latest and historical MF snapshots
- `data/kite/companies/`: per-company cached company-analysis artifacts
- `data/kite/provider_compare/`: side-by-side provider comparison files such as `BSE_yfinance.json` and `BSE_nse_india.json`
- `data/console_exports/`: local notes and reference exports
- `reports/`: portfolio reports
- `reports/research/`: per-holding research files, combined digest, and index artifacts

UI companion:

- `../artha-ui`: Next.js 14 dashboard for holdings, reports, stock detail, and live run monitoring
- `../artha-ui/.env.local`: set `NEXT_PUBLIC_API_URL=http://localhost:8000`
- `../artha-ui/package.json`: includes `npm run dev:all` to start both the FastAPI backend and the Next.js frontend

## Model Split

- `MODEL`: main Artha agent, portfolio synthesis, and deep-research orchestration defaults to `claude-sonnet-4-6`
- `ANALYST_MODEL`: per-holding analyst sub-agents default to `claude-haiku-4-5`
- `ANALYST_MAX_TOKENS`: lower output cap for company artifact generation
- `ANALYST_MAX_SEARCHES`: max Tavily searches per analyst or deep-research holding, default `3`
- `ANALYST_PARALLELISM`: max concurrent analyst refresh jobs, default `2`
- `ANALYST_MIN_START_INTERVAL_SECONDS`: per-holding stagger used before analyst refresh starts, default `3`
- `SUMMARY_MAX_TOKENS`: lower output cap for the final Sonnet summary
- `COMPANY_ANALYSIS_MAX_AGE_DAYS`: company-analysis cache freshness window, default `7`

## Warning

Artha provides analysis only. Never execute trades automatically from its output.

## Repository Automation

The repository includes a baseline GitHub automation setup:

- `.github/workflows/ci.yml`: required PR CI for Python 3.11, pytest, and coverage with an 80% minimum threshold
- `.github/workflows/codeql.yml`: required CodeQL scanning for Python on pull requests, pushes to `main`, and a weekly schedule
- `.github/dependabot.yml`: weekly grouped updates for Python dependencies and GitHub Actions
- `.github/copilot-instructions.md`: repository-wide Copilot guidance for review and code generation
- `.coderabbit.yaml`: CodeRabbit review guidance with automatic PR review enabled once the GitHub app is installed

Recommended GitHub repository settings:

- require the `CI` and `CodeQL` checks before merge
- require at least one human approval before merge
- enable secret scanning and push protection
- install the CodeRabbit GitHub app if you want automatic AI PR reviews on this public repository

## Copilot Repo Guidance

Repo-local Copilot assets currently installed under `.github/skills/`:

- `agentic-eval`
- `autoresearch`
- `codeql`
- `conventional-commit`
- `create-readme`
- `dependabot`
- `doublecheck`
- `eval-driven-dev`
- `gh-cli`
- `github-issues`
- `polyglot-test-agent`
- `prd`
- `prompt-builder`
- `pytest-coverage`
- `python-mcp-server-generator`
- `refactor`
- `secret-scanning`
- `shadcn-component-discovery`
- `sql-code-review`
- `sql-optimization`
- `suggest-awesome-github-copilot-agents`
- `suggest-awesome-github-copilot-instructions`
- `suggest-awesome-github-copilot-skills`

Repo-local custom agents are present under `.github/agents/`. Repository-wide Copilot instructions are present in `.github/copilot-instructions.md`. There are currently no path-specific custom instruction files under `.github/instructions/`.

For this repository, the highest-value skills are:

- `eval-driven-dev`, `agentic-eval`, and `pytest-coverage` for LLM quality gates and regression protection
- `doublecheck` for verifying factual and numerical claims in financial analysis output
- `refactor` for surgical maintainability changes
- `create-readme` to keep repository guidance accurate
- `dependabot`, `codeql`, and `secret-scanning` for repository guardrails
- the three `suggest-awesome-*` skills for recommending missing Copilot assets without installing them blindly
