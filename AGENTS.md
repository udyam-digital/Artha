# AGENTS.md

## Purpose

This repository contains `Artha`, a read-only portfolio research and rebalancing agent for Indian equity portfolios. It uses:

- Anthropic for reasoning and tool use
- Zerodha/Kite via MCP for live portfolio data
- Local markdown skills for system instructions

The project is analysis-only. Do not add auto-trading or order-placement behavior unless explicitly requested.

## Main Entry Points

- `main.py`: CLI entrypoint for auth, sync, report generation, and deep research
- `agent.py`: `ArthaAgent` loop, prompt construction, tool handling, and final report parsing
- `tools.py`: Kite MCP client, tool execution, native tool definitions, and helper parsing
- `kite_runtime.py`: hosted Kite auth/session checks plus fresh equity and MF snapshot sync
- `snapshot_store.py`: local snapshot and research artifact persistence
- `research.py`: deep-research orchestration for one holding-level sub-agent per equity and MF holding
- `rebalance.py`: Portfolio drift and action calculation
- `models.py`: Pydantic schemas for holdings, snapshots, analyses, reports, and research artifacts
- `config.py`: environment-driven settings and directory initialization

## Runtime Flow

1. Load settings from `.env`.
2. Connect to Kite MCP over HTTP or stdio.
3. Pull fresh equity holdings, MF holdings, margins, and profile data.
4. Persist fresh local snapshots under `data/kite/portfolio/` and `data/kite/mf/`.
5. Let the LLM call tools, especially `web_search`, before producing a final report.
6. Validate the final response as `PortfolioReport`.
7. Persist JSON outputs under `reports/`.

If report parsing fails, the app falls back to a rebalance-oriented report with captured errors.

## Supported Commands

```bash
.venv/bin/python main.py kite-login
.venv/bin/python main.py kite-sync
.venv/bin/python main.py rebalance
.venv/bin/python main.py holdings
.venv/bin/python main.py run
.venv/bin/python main.py run --ticker KPITTECH
.venv/bin/python main.py run --rebalance-only
.venv/bin/python main.py research
```

## Environment

Minimum required variables:

```env
ANTHROPIC_API_KEY=
MODEL=claude-sonnet-4-6
KITE_MCP_URL=https://mcp.kite.trade/mcp
```

Optional runtime configuration:

```env
MAX_TOKENS=8096
MAX_ITERATIONS=10
REPORTS_DIR=./reports
LOG_LEVEL=INFO
KITE_MCP_COMMAND=
KITE_MCP_ARGS=[]
KITE_MCP_ENV_JSON={}
KITE_MCP_TIMEOUT_SECONDS=30
KITE_DATA_DIR=./data/kite
```

Notes:

- `KITE_MCP_URL` is the default path. `KITE_MCP_COMMAND` is the fallback for stdio-based MCP.
- `KITE_MCP_ARGS` must be valid JSON array text when set through `.env`.
- `KITE_MCP_ENV_JSON` must be valid JSON object text when set through `.env`.

## Data Layout

- `skills/`: system prompt source material
- `data/kite/auth/`: saved Kite auth artifacts
- `data/kite/portfolio/`: saved portfolio snapshots
- `data/kite/mf/`: saved MF snapshots
- `reports/`: generated JSON reports
- `reports/research/`: per-holding research artifacts and combined digests
- `tests/`: unit tests

## Agent Constraints

- Preserve read-only portfolio behavior.
- Use live Kite data for holdings-based decisions.
- Keep passive instruments out of equity rebalance actions as defined in `rebalance.py`.
- Do not include MF holdings in equity rebalance actions.
- Persist MF holdings locally even though they are excluded from rebalancing.
- For full runs, retain the deep-research behavior built around `web_search`.
- Keep final LLM output wrapped in `<artha_report>...</artha_report>` and valid against `PortfolioReport`.
- Prefer graceful degradation over crashes when tool output is partial or malformed.

## Development Rules

- Target Python 3.11+.
- Keep types explicit and compatible with current Pydantic usage.
- Follow the existing async structure around Anthropic and MCP.
- Avoid embedding business logic in the CLI when it belongs in `agent.py`, `tools.py`, or `rebalance.py`.
- When changing prompts or tool definitions, update tests that assert prompt/tool behavior.

## Test Commands

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
```

Focused test runs:

```bash
.venv/bin/python -m pytest tests/test_agent.py
.venv/bin/python -m pytest tests/test_tools.py
.venv/bin/python -m pytest tests/test_rebalance.py
```

## Common Change Patterns

### Add a new CLI capability

1. Extend `build_parser()` in `main.py`.
2. Add a handler in `main.py`.
3. Put reusable logic outside the CLI layer.
4. Add or update tests.

### Add or change a tool

1. Update tool definitions in `tools.py`.
2. Implement execution and error handling in `tools.py`.
3. Wire the tool into `agent.py` if needed.
4. Add tests for both happy path and malformed payloads.

### Adjust portfolio rules

1. Update `rebalance.py` or `skills/portfolio_rules.md`, depending on whether the rule is deterministic logic or prompt guidance.
2. Confirm passive-instrument handling still matches expectations.
3. Run `tests/test_rebalance.py` and related agent tests.

## Safe Defaults For Future Agents

- Assume this repo is production-adjacent financial analysis software.
- Bias toward transparent errors, conservative behavior, and auditability.
- Prefer small, test-backed changes over prompt-only fixes when the issue is deterministic.
- Do not remove existing fallbacks unless you replace them with stricter, tested behavior.


Always USE-
- Always use context7 mcp, and locally save responses and always be context aware, and keep updating it.
- Make a suggestions.md file, where you give your suggestions and give these Like an expert Gen Ai Architect
- Always Update README.md so it shows exactly what we have currently 
