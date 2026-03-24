# AGENTS.md

## Purpose

This repository contains `Artha`, a read-only portfolio research and rebalancing agent for Indian equity portfolios. It uses:

- Anthropic for reasoning and tool use
- Zerodha/Kite via MCP for live portfolio data
- Local markdown skills for system instructions

The project is analysis-only. Do not add auto-trading or order-placement behavior unless explicitly requested.

## Main Entry Points

- `main.py`: CLI entrypoint for auth, sync, report generation, and deep research
- `mcp_server.py`: Artha MCP server entrypoint — exposes read-only analysis as MCP tools
- `application/agent.py`: `ArthaAgent` loop, prompt construction, tool handling, and final report parsing
- `application/orchestrator.py`: verdict-driven run pipeline — the main `run` entrypoint
- `application/research_orchestrator.py`: deep-research orchestration for one holding-level sub-agent per equity and MF holding
- `kite/tools.py`: Kite MCP client, tool execution, native tool definitions, and helper parsing
- `kite/runtime.py`: hosted Kite auth/session checks plus fresh equity and MF snapshot sync
- `persistence/store.py`: local snapshot and research artifact persistence
- `rebalance.py`: Portfolio drift and action calculation
- `models.py`: Pydantic schemas for holdings, snapshots, analyses, reports, and research artifacts
- `config.py`: environment-driven settings and directory initialization
- `analysis/analyst.py`: per-holding analysis on Claude Haiku, parallelized
- `analysis/verify.py`: deterministic numeric verifier for portfolio weights and rebalance math

## Runtime Flow

1. Load settings from `.env`.
2. Connect to Kite MCP over HTTP or stdio (`kite/runtime.py`).
3. Pull fresh equity holdings, MF holdings, margins, and profile data.
4. Persist fresh local snapshots under `data/kite/portfolio/` and `data/kite/mf/`.
5. Run `analysis/analyst.py` (Claude Haiku) per-holding in parallel — refresh stale or price-moved artifacts.
6. Run deterministic rebalance math in `rebalance.py`.
7. Synthesize final report via `application/agent.py` (Claude Sonnet).
8. Validate the final response as `PortfolioReport`.
9. Persist JSON outputs under `reports/`; update `reports/index.json` sidecar and `reports/manifests/`.

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
.venv/bin/python main.py analyst --ticker BSE
.venv/bin/python main.py compare-providers --ticker BSE
.venv/bin/python main.py research
.venv/bin/python main.py usage-report --last 10
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
- For full runs and research flows, retain the Tavily-backed research behavior built around `tavily_search`.
- Keep final LLM output wrapped in `<artha_report>...</artha_report>` and valid against `PortfolioReport`.
- Prefer graceful degradation over crashes when tool output is partial or malformed.

## Development Rules

- Target Python 3.11+.
- Keep types explicit and compatible with current Pydantic usage.
- Follow the existing async structure around Anthropic and MCP.
- Avoid embedding business logic in the CLI when it belongs in `application/agent.py`, `kite/tools.py`, or `rebalance.py`.
- Path-specific Copilot review instructions are in `.github/instructions/` — keep them current when adding new modules.
- When changing prompts or tool definitions, update tests that assert prompt/tool behavior.

## Skill Usage

- Before doing substantial implementation, testing, verification, review, or documentation work, scan `.github/skills/` and register the currently available repo-local skills for the task at hand.
- Maintain awareness of the full repo-local skill inventory and use every skill that is relevant to the current task. Do not load unrelated skills just because they exist.
- If a matching local skill exists, follow it unless it conflicts with higher-priority instructions in this file.
- Prefer the narrowest set of relevant skills that fully covers the task.
- When the task involves improving repository guidance, developer workflow, Copilot setup, agent configuration, or missing repo automation, explicitly consider the three suggestor skills:
  - `.github/skills/suggest-awesome-github-copilot-skills/`
  - `.github/skills/suggest-awesome-github-copilot-agents/`
  - `.github/skills/suggest-awesome-github-copilot-instructions/`
- Use the suggestor skills to identify missing or outdated skills, custom agents, and custom instructions. Do not install or update suggested assets unless explicitly requested.
- Typical cases:
  - use `.github/skills/pytest-coverage/` when changing tested Python behavior or adding coverage
  - use `.github/skills/doublecheck/` before finalizing non-trivial changes that need a verification pass
  - use `.github/skills/create-readme/` when README changes are needed
  - use `.github/skills/conventional-commit/` when preparing commit messages or commit hygiene guidance
  - use `.github/skills/dependabot/` when changing dependency update policy or repo maintenance automation
  - use `.github/skills/eval-driven-dev/` when designing evaluation-oriented development loops
  - use `.github/skills/gh-cli/` when repo automation depends on GitHub CLI workflows
  - use `.github/skills/python-mcp-server-generator/` when adding or restructuring Python MCP server behavior
  - use `.github/skills/sql-optimization/` when query design or SQL performance becomes relevant
  - use `.github/skills/agentic-eval/` when evaluating AI agent outputs
  - use `.github/skills/autoresearch/` for autonomous experimentation and optimization
  - use `.github/skills/codeql/` for security scanning and CodeQL configuration
  - use `.github/skills/github-issues/` for GitHub issue management and tracking
  - use `.github/skills/polyglot-test-agent/` for generating comprehensive unit tests
  - use `.github/skills/prd/` for creating product requirements documents
  - use `.github/skills/prompt-builder/` for building and refining prompts
  - use `.github/skills/refactor/` for code refactoring and maintainability improvements
  - use `.github/skills/secret-scanning/` for configuring secret scanning and remediation
  - use `.github/skills/sql-code-review/` for SQL code review and security analysis
  - use `.github/skills/agent-governance/` when adding or reviewing tool-access controls, policy enforcement, rate limits, audit trails, or trust boundaries in the agent loop
  - use `.github/skills/ai-prompt-engineering-safety-review/` when creating, changing, or auditing any system prompt, skill prompt, or LLM instruction in `skills/` or `agent.py`
  - use `.github/skills/architecture-blueprint-generator/` when producing or updating architectural documentation, diagrams, or `docs/timeline.md` entries
- Treat repo-local skills as execution guidance for this codebase, not just reference material.

## Custom Agent Usage

- Before substantial architecture, refactoring, API-boundary, or governance work, scan `.github/agents/` and register the relevant repo-local custom agents for the task at hand.
- Use the narrowest relevant agent set; do not invoke unrelated agents just because they are installed.
- Treat repo-local custom agents as execution guidance for this codebase in the same way as repo-local skills.
- For codebase organization and structural refactoring discussions, start with `Context Architect`, then use `API Architect` when the CLI/API/service boundary is involved, and capture the chosen structure with `ADR Generator`.
- Use `Critical thinking mode instructions` to stress-test large refactor plans before moving modules or changing boundaries.
- Use `Agent Governance Reviewer` when the change affects agent safety, auditability, trust boundaries, tool governance, or financial-analysis controls.
- Use `Doublecheck` when verifying factual, numerical, or source-backed claims in generated reports, recommendations, or repository guidance.

### Current Repo-Local Custom Agent Registry

Current agents available under `.github/agents/`:

- `adr-generator.agent.md`
- `agent-governance-reviewer.agent.md`
- `api-architect.agent.md`
- `context-architect.agent.md`
- `critical-thinking.agent.md`
- `doublecheck.agent.md`

Refresh this registry whenever the contents of `.github/agents/` change so the instructions remain accurate.

### Current Repo-Local Skill Registry

Current skills available under `.github/skills/`:

- `agent-governance`
- `agentic-eval`
- `ai-prompt-engineering-safety-review`
- `architecture-blueprint-generator`
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

Refresh this registry whenever the contents of `.github/skills/` change so the instructions remain accurate.

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
.venv/bin/python -m pytest tests/test_mcp_server.py
.venv/bin/python -m pytest tests/test_api_run.py
```

## Common Change Patterns

### Add a new CLI capability

1. Extend `build_parser()` in `main.py`.
2. Add a handler in `main.py`.
3. Put reusable logic outside the CLI layer (in `application/`, `analysis/`, `kite/`, or `rebalance.py`).
4. Add or update tests.

### Add or change a tool

1. Update tool definitions in `kite/tools.py`.
2. Implement execution and error handling in `kite/tools.py`.
3. Wire the tool into `application/agent.py` if needed.
4. Add tests for both happy path and malformed payloads.

### Adjust portfolio rules

1. Update `rebalance.py` or `skills/portfolio_rules.md`, depending on whether the rule is deterministic logic or prompt guidance.
2. Confirm passive-instrument handling still matches expectations.
3. Run `tests/test_rebalance.py` and related agent tests.

### Change analyst behavior

1. Update `analysis/analyst.py` or `skills/analyst_prompt.md`.
2. Artifact schema changes go in `models.py`.
3. Update `tests/test_analyst.py` and `tests/test_analyst_evals.py`.

## Safe Defaults For Future Agents

- Assume this repo is production-adjacent financial analysis software.
- Bias toward transparent errors, conservative behavior, and auditability.
- Prefer small, test-backed changes over prompt-only fixes when the issue is deterministic.
- Do not remove existing fallbacks unless you replace them with stricter, tested behavior.

## Always Use

- Use Context7 when current library, framework, or API documentation is relevant. Save useful findings locally when they materially inform implementation or maintenance decisions.
- Stay context-aware across the repo. Reconcile changes with existing architecture, prompts, tools, tests, and persisted artifacts before editing behavior.
- Maintain a `suggestions.md` file with practical recommendations written from a senior GenAI architect perspective. Keep it current when new gaps, upgrades, or repo improvements are identified.
- Keep `README.md` aligned with the current repository behavior, setup, commands, and capabilities whenever a change makes the existing README inaccurate.
