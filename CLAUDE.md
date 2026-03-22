# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**Artha** is a read-only portfolio research and rebalancing agent for Indian equity portfolios. It uses Claude (Anthropic), Tavily web research, and Zerodha/Kite MCP for live portfolio data. **Do not add auto-trading or order-placement behavior.**

## Commands

```bash
# Setup
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # then fill in ANTHROPIC_API_KEY, TAVILY_API_KEY

# Kite auth
.venv/bin/python main.py kite-login
.venv/bin/python main.py kite-sync

# Analysis runs
.venv/bin/python main.py run                     # Full portfolio analysis
.venv/bin/python main.py run --ticker KPITTECH   # Single stock deep dive
.venv/bin/python main.py run --rebalance-only    # Skip LLM, math only
.venv/bin/python main.py rebalance               # Drift math, no LLM
.venv/bin/python main.py research                # Deep research all holdings
.venv/bin/python main.py holdings                # Print current holdings
.venv/bin/python main.py usage-report --last 10  # LLM cost report

# API server (for artha-ui companion at ../artha-ui)
.venv/bin/python -m uvicorn api.main:app --reload --port 8000

# Tests
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_rebalance.py   # focused run
.venv/bin/python -m pytest tests/test_analyst.py -k test_name  # single test
```

## Architecture

### Package Layout

```
main.py                  # CLI only — no business logic here
config.py                # Pydantic-settings; JSON fields KITE_MCP_ARGS/KITE_MCP_ENV_JSON need valid JSON in .env
models.py                # All Pydantic schemas: Holding, PortfolioSnapshot, StockVerdict, PortfolioReport, CompanyAnalysisArtifact
rebalance.py             # Deterministic drift math; PASSIVE_INSTRUMENTS excluded from rebalance
reliability.py           # Retry helpers, FullRunFailed exception

application/
  orchestrator.py        # Verdict-driven run pipeline — the main "run" entrypoint
  agent.py               # ArthaAgent loop: prompt construction, tool execution, <artha_report> parsing
  research.py            # Deep-research orchestration (one sub-agent per holding)
  reporting.py           # Report formatting helpers

analysis/
  analyst.py             # CompanyAnalyzer — per-holding analysis on Claude Haiku, parallelized
  company.py             # Company artifact retrieval, freshness checks (time + price-move), verdict conversion
  judge.py               # Verdict/confidence evaluation logic (LLM-as-judge, factual grounding)
  verify.py              # Deterministic numeric verifier: verify_portfolio_weights, verify_rebalance_consistency
  fiscal.py              # Fiscal data helpers

kite/
  client.py              # KiteMCPClient (HTTP or stdio transport)
  runtime.py             # Browser auth, same-day snapshot reuse, sync_kite_data()
  tools.py               # Tool implementations: kite_get_portfolio, kite_get_price_history, etc.

search/
  tavily.py              # Tavily web search wrapper

persistence/
  store.py               # Snapshot and artifact persistence (replaces old snapshot_store.py)

observability/
  telemetry.py           # OpenTelemetry / Langfuse trace setup
  usage.py               # LLM cost tracking, run summaries, JSONL logging
  token_budget.py        # Sliding token budget for TPM compliance
  langfuse_client.py     # Langfuse-specific client initialization

api/
  main.py                # FastAPI: /api/holdings, /api/mf-holdings, /api/reports, /api/reports/latest, /api/run (SSE)

skills/                  # System prompt source material for LLM calls
  analyst_prompt.md
  portfolio_rules.md
  equity_analysis.md
```

### Request Flow

```
main.py (CLI)
  └─ application/orchestrator.py: run_full_analysis()
       1. kite/runtime.py: sync_kite_data() → data/kite/portfolio/ + data/kite/mf/
       2. analysis/verify.py: verify_portfolio_weights() → log weight/value sanity warnings
       3. kite/tools.py: kite_get_price_history() per holding (52w candles)
       4. persistence/store.py: load cached company artifacts (TTL: COMPANY_ANALYSIS_MAX_AGE_DAYS + price-move threshold)
       5. analysis/analyst.py: CompanyAnalyzer (Claude Haiku) → refresh stale/missing/price-moved artifacts
       6. rebalance.py: calculate_rebalancing_actions() → deterministic drift math
       7. application/agent.py: ArthaAgent (Claude Sonnet) → final synthesis → PortfolioReport
       8. persistence/store.py: persist to reports/YYYYMMDD_HHMMSS_artha_report.json + update reports/index.json
       9. persistence/store.py: save_run_manifest() → reports/manifests/{run_id}_manifest.json
```

### Model Split

- **Claude Haiku** (`ANALYST_MODEL`): Per-holding analysis in `analysis/analyst.py` — cost-optimized, parallelized with staggered starts and token budgets
- **Claude Sonnet** (`MODEL`): Final portfolio synthesis in `application/agent.py` — quality-optimized, no-tool summary call

### Data Layout

```
data/kite/portfolio/      # Equity snapshots (reused within same day)
data/kite/mf/             # MF snapshots
data/kite/companies/      # Per-company analysis artifacts ({ticker}.json, TTL=7 days or price-move threshold)
reports/                  # PortfolioReport JSON outputs
reports/index.json        # Append-only report sidecar (id, filename, generated_at, verdict_counts, error_count)
reports/manifests/        # Per-run evidence manifests ({run_id}_manifest.json)
reports/usage/            # LLM cost JSONL (llm_usage_*, run_summaries, run_errors)
reports/research/         # Per-holding deep research artifacts
```

## Constraints

- Final LLM output must be wrapped in `<artha_report>...</artha_report>` and validate as `PortfolioReport`
- Prefer graceful degradation over crashes when tool output is partial or malformed
- Keep business logic out of `main.py`; it belongs in `application/`, `analysis/`, `kite/`, or `rebalance.py`
- Target Python 3.11+; keep types explicit and compatible with current Pydantic usage
- Follow the existing async structure around Anthropic and MCP
- MF holdings are informational only — never included in equity rebalance actions
- Passive instruments (LIQUIDBEES, NIFTYBEES, GOLDCASE, SILVERCASE) are excluded from analyst fan-out and rebalancing but kept in portfolio totals

## Common Change Patterns

**Add a CLI command:** Extend `build_parser()` and add handler in `main.py`, put logic in appropriate package.

**Add/change a tool:** Update definitions and execution in `kite/tools.py`, wire into `application/agent.py`, add tests for happy path and malformed payloads.

**Adjust portfolio rules:** Deterministic logic → `rebalance.py`; prompt guidance → `skills/portfolio_rules.md`. Run `tests/test_rebalance.py`.

**Change analyst behavior:** Update `analysis/analyst.py` or `skills/analyst_prompt.md`. Artifact schema changes go in `models.py`. Update `tests/test_analyst.py`.

**Change prompts or tool definitions:** Update tests that assert prompt/tool behavior.

## Environment Variables

Minimum required:
```
ANTHROPIC_API_KEY=
TAVILY_API_KEY=
KITE_MCP_URL=https://mcp.kite.trade/mcp
```

Key optional settings (see `config.py` for full list):
- `MODEL` / `ANALYST_MODEL`: Claude model routing
- `ANALYST_PARALLELISM`, `ANALYST_MIN_START_INTERVAL_SECONDS`, `HAIKU_INPUT_TPM`, `HAIKU_OUTPUT_TPM`: rate limiting
- `COMPANY_ANALYSIS_MAX_AGE_DAYS`: artifact cache TTL (default 7)
- `COMPANY_CACHE_PRICE_MOVE_THRESHOLD_PCT`: invalidate cache if price moved more than this % since last analysis (default 15.0)
- `KITE_MCP_ARGS` / `KITE_MCP_ENV_JSON`: must be valid JSON strings in `.env`
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`: optional Langfuse tracing
- `OTEL_EXPORTER_OTLP_ENDPOINT`: optional OTLP backend

## Custom Agents and Skills

Custom agents under `.github/agents/` and skills under `.github/skills/` are for GitHub Copilot workflows. When relevant:

- `Context Architect` → `API Architect` → `ADR Generator` for structural work
- `Agent Governance Reviewer` for safety/auditability changes in this financial-analysis system
- `Doublecheck` for verifying factual or numerical claims

See `AGENTS.md` for the full registries and development ruleset.
