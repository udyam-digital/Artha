# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**Artha** is a read-only portfolio research and rebalancing agent for Indian equity portfolios. It uses Claude (Anthropic), Tavily web research, and Zerodha/Kite MCP for live portfolio data. **Do not add auto-trading or order-placement behavior.**

## Commands

```bash
# Setup
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # then fill in ANTHROPIC_API_KEY

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
.venv/bin/python -m pytest tests/test_agent.py
```

## Architecture

### Request Flow

```
main.py (CLI)
  └─ orchestrator.py: run_full_analysis()
       1. kite_runtime.py: sync_kite_data() → data/kite/portfolio/ + data/kite/mf/
       2. tools.py: kite_get_price_history() for each holding (52w candles)
       3. snapshot_store.py: load data/companies/{ticker}.json (cache TTL: COMPANY_ANALYSIS_MAX_AGE_DAYS)
       4. analyst.py: CompanyAnalyzer (Claude Haiku) → refreshes stale/missing artifacts
       5. rebalance.py: calculate_rebalancing_actions() → deterministic drift math
       6. agent.py: ArthaAgent (Claude Sonnet) → final synthesis → PortfolioReport
       7. snapshot_store.py: persist to reports/YYYYMMDD_HHMMSS_artha_report.json
```

### Model Split

- **Claude Haiku** (`ANALYST_MODEL`): Per-holding analysis in `analyst.py` — cost-optimized, parallelized
- **Claude Sonnet** (`MODEL`): Final portfolio synthesis in `agent.py` — quality-optimized

### Key Files

| File | Role |
|------|------|
| `agent.py` | ArthaAgent loop: prompt construction, tool execution, parses `<artha_report>` wrapper |
| `tools.py` | KiteMCPClient (HTTP or stdio), tool implementations, `kite_get_portfolio/profile/price_history`, `tavily_search` |
| `orchestrator.py` | Verdict-driven run pipeline, merges analyst verdicts with rebalancing math |
| `analyst.py` | Per-holding CompanyAnalyzer using Haiku, outputs `CompanyAnalysisArtifact` |
| `models.py` | Pydantic schemas: `Holding`, `PortfolioSnapshot`, `StockVerdict`, `PortfolioReport`, `CompanyAnalysisArtifact` |
| `rebalance.py` | Drift calculation; passive instruments (LIQUIDBEES, NIFTYBEES, GOLDCASE, SILVERCASE) and MFs are excluded |
| `snapshot_store.py` | Persistence for portfolio snapshots, company artifacts (with legacy field migration) |
| `kite_runtime.py` | Kite browser auth, same-day snapshot reuse |
| `config.py` | Pydantic-settings config; JSON fields `KITE_MCP_ARGS`/`KITE_MCP_ENV_JSON` require valid JSON text in `.env` |
| `api/main.py` | FastAPI: `/api/holdings`, `/api/reports`, `/api/reports/latest`, `/api/run` (SSE streaming) |
| `skills/` | System prompt source material: `analyst_prompt.md`, `portfolio_rules.md`, `equity_analysis.md` |

### Data Layout

```
data/kite/portfolio/      # Equity snapshots (reused within same day)
data/kite/mf/             # MF snapshots
data/companies/           # Per-company analysis artifacts ({ticker}.json, TTL=7 days)
reports/                  # PortfolioReport JSON outputs
reports/usage/            # LLM cost JSONL (llm_usage_*, run_summaries, run_errors)
reports/research/         # Per-holding deep research artifacts
```

### Constraints

- Final LLM output must be wrapped in `<artha_report>...</artha_report>` and validate as `PortfolioReport`
- Prefer graceful degradation over crashes when tool output is partial or malformed
- Keep business logic out of `main.py`; it belongs in `agent.py`, `tools.py`, or `rebalance.py`
- Target Python 3.11+; keep types explicit and compatible with current Pydantic usage
- Follow the existing async structure around Anthropic and MCP

## Common Change Patterns

**Add a CLI command:** Extend `build_parser()` and add handler in `main.py`, put logic elsewhere.

**Add/change a tool:** Update definitions and execution in `tools.py`, wire into `agent.py`, add tests for happy path and malformed payloads.

**Adjust portfolio rules:** Deterministic logic → `rebalance.py`; prompt guidance → `skills/portfolio_rules.md`. Run `tests/test_rebalance.py`.

**Change prompts or tool definitions:** Update tests that assert prompt/tool behavior.

## Additional Guidance

See `AGENTS.md` for the full development ruleset, skill registry (`.github/skills/`), and detailed environment variable reference.
