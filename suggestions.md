# Suggestions for Artha

This file tracks practical improvement recommendations for Artha from a senior GenAI architect perspective. The emphasis is on auditability, regression resistance, conservative financial behavior, and keeping repository guidance aligned with the actual codebase.

## New Findings

### Provider comparison (resolved)

Yahoo Finance and NSE India are now live in the codebase (`search/`, `kite/tools.py`). Findings confirmed:

- Yahoo Finance: strong general-purpose enrichment for NSE names — fundamentals and flat metrics. (`tests/test_yfinance_tool.py` covers this.)
- `stock-nse-india`: good for exchange-specific metadata and 52-week data, but stdio transport prints banner text before JSON-RPC frames. Should eventually be replaced with a quieter wrapper.
- Alpha Vantage: removed from active path — not worth the rate-limit pressure for Indian equities.
- Runtime defaults are now version-pinned: Yahoo Finance MCP is pinned to Git commit `f54e92663d23282fef913f47f6b1bd603e861cbb`, and `stock-nse-india` is pinned to npm version `1.3.0`.

### MoSPI MCP integration

MoSPI data is now available via the `mcp__claude_ai_MoSPI_Statistics__` MCP server tools (step1–step4 flow). Suggestion #8 (authenticated MoSPI access) is partially addressed. Remaining gap: the endpoint contracts and field names are not yet pinned in fixtures or code comments, and there is no integration smoke test validating saved real payloads for CPI, IIP, and GDP.

### MCP server

`mcp_server.py` exists on the `adding_mcp` branch, exposing Artha capabilities as MCP tools. This is new surface area — ensure tool schemas are tested and that no order-placement tools are accidentally exposed (per the read-only constraint in `CLAUDE.md`).

That test gap is now closed at a baseline level with `tests/test_mcp_server.py`, but the higher-value remaining check is an integration smoke test against a real saved report/artifact set.

## Highest Priority

### 1. Extend eval-driven quality gates for report behavior (further extended)

Relevant skills: `eval-driven-dev`, `agentic-eval`, `pytest-coverage`

`tests/test_analyst_evals.py` was added (commit `e0dea58`) and further extended with `PortfolioReport`-level eval tests:

- `test_eval_portfolio_report_verdict_distribution`: verifies BUY/HOLD/SELL bucket counting
- `test_eval_rebalance_math_consistency`: verifies total_buy_required matches sum of BUY rupees
- `test_eval_error_fallback_report`: verifies error reports are still valid PortfolioReport instances

The next gap is saved portfolio snapshot fixtures as eval inputs and prompt-change tests.

### 2. Add deterministic verification for financial claims (Python-side added)

Relevant skill: `doublecheck`

The "LLM as a judge" layer was added (commit `9dcfe42`, `analysis/judge.py`). This evaluates verdict/confidence quality. A new Python-side verifier has been added at `analysis/verify.py`:

- `verify_portfolio_weights()`: checks weight sums, value totals, and per-holding sanity
- `verify_rebalance_consistency()`: checks that BUY/SELL actions have non-zero rupees and HOLD actions have zero

`verify_portfolio_weights()` is now called in `application/orchestrator.py` after Kite sync, with warnings logged. The remaining gap is cross-checking numeric accuracy against raw source data for free-text summary claims.

### 3. Raise the CI and review bar further (DONE — ruff lint step added)

Relevant skills: `dependabot`, `codeql`, `secret-scanning`, `gh-cli`

The baseline repo automation now exists. Completed:

- `ruff check` step added to `.github/workflows/ci.yml` as a parallel `lint` job
- Path-specific Copilot instructions added under `.github/instructions/` (code-review-generic, python-mcp-server, ai-prompt-engineering-safety, github-actions)

Remaining:

- make branch protection require `CI`, `CodeQL`, and one human approval
- enable repository secret scanning and push protection in GitHub settings
- install and tune CodeRabbit on the public repo now that public-repo reviews are free

## Medium Priority

### 4. Move API report listing off full-file reparsing (DONE)

Relevant files: `api/main.py`, `persistence/store.py`, `application/reporting.py`

`reports/index.json` sidecar is now maintained by `save_report()` in `persistence/store.py`. Each entry contains `{id, filename, generated_at, total_value, verdict_counts, error_count}`. `list_report_items()` in `application/reporting.py` uses this as a fast path and falls back to full reparsing when the index doesn't exist.

### 5. Make run progress a structured interface (DONE)

Relevant files: `application/orchestrator.py`, `api/main.py`

The orchestrator already emits typed `PhaseEvent` and `AnalystCompleteEvent` TypedDict objects via `RunEventCallback`. The FastAPI layer in `api/main.py` converts these to SSE events with structured JSON payloads. No terminal-text parsing is used.

### 6. Improve auth/session reliability around Kite MCP (largely addressed)

Relevant files: `kite/runtime.py`, `kite/tools.py`

`sync_kite_data_with_client()` in `kite/runtime.py` already auto-re-authenticates via `profile_requires_login()` + `wait_for_kite_login()`. The session handoff between `kite-login` and `run` is explicit. The main remaining gap is a race condition if the session expires mid-run; consider adding a heartbeat or re-auth mid-run capability for long analysis runs.

### 7. Make cached analysis refresh more event-aware (price-move threshold added)

Relevant files: `analysis/company.py`, `application/orchestrator.py`, `config.py`

`is_company_artifact_fresh()` now accepts a `current_price` parameter. If the price has moved more than `COMPANY_CACHE_PRICE_MOVE_THRESHOLD_PCT` (default 15%) from the cached price, the artifact is treated as stale. Both `get_company_artifact_and_verdict()` and `_holding_requires_refresh()` pass the current holding price.

Remaining event-awareness gaps: earnings date triggers, new filing detection, and stale source URL detection.

### 8. Add authenticated and version-pinned MoSPI data access (smoke tests added)

Relevant files: `kite/tools.py`, `config.py`, `tests/test_macro_context.py`

MoSPI parser smoke tests have been added to `tests/test_macro_context.py`:

- `test_extract_mospi_records_cpi_payload`: validates CPI payload shape parsing
- `test_extract_mospi_records_iip_payload`: validates IIP payload shape parsing
- `test_find_value_case_insensitive`: validates case-insensitive key lookup

Remaining: register a proper MoSPI API token if the production endpoint requires it, and pin exact endpoint field names in code comments.

## Lower Priority

### 9. Formalize the MF API contract (DONE)

Relevant files: `api/main.py`, `models.py`

`GET /api/mf-holdings` endpoint added in `api/main.py`. Returns the latest saved `MFSnapshot` directly. Returns 404 if no MF snapshot exists. The equity holdings endpoint (`/api/holdings`) continues to include the MF snapshot for convenience.

### 10. Add better run manifests and evidence logs (DONE)

Relevant files: `persistence/store.py`, `application/orchestrator.py`

`save_run_manifest()` added to `persistence/store.py`. Manifests are saved to `reports/manifests/{run_id}_manifest.json` after each full run. Fields include: `run_id`, `generated_at`, `snapshot_paths_used`, `analyst_inputs`, `elapsed_seconds`, `verdict_counts`, `error_count`, `failure_reasons`.

Remaining: per-verdict evidence trails (source URLs with timestamps) rather than just final source URLs in the verdict output.

### 11. Factor service-layer modules more explicitly

Relevant files: `application/orchestrator.py`, `analysis/analyst.py`, `application/research.py`, `kite/runtime.py`

These files already form a service layer. If the codebase grows, move them under a dedicated package boundary so orchestration, tool execution, and persistence responsibilities stay clear.

## Copilot Improvements

### Current state (updated)

- Repo-local skills exist under `.github/skills/`
- Repo-local custom agents exist under `.github/agents/`
- Repository-wide Copilot instructions exist at `.github/copilot-instructions.md`
- Path-specific instruction files now exist under `.github/instructions/`:
  - `code-review-generic.instructions.md` — applies to all files
  - `python-mcp-server.instructions.md` — applies to `kite/**`, `mcp_server.py`, `search/**`
  - `ai-prompt-engineering-safety.instructions.md` — applies to `skills/**`, `analysis/analyst.py`, `analysis/judge.py`, `application/agent.py`
  - `github-actions.instructions.md` — applies to `.github/workflows/**`

### Recommended custom instructions to add (done)

All four recommended instruction files have been added. Do not add framework-specific instructions that do not match this repo (no .NET, Java, or frontend-only packs).

### Recommended custom agents to add

The latest `awesome-copilot` agent catalog suggests a small set that would add value here:

| Agent | Why it fits Artha |
| --- | --- |
| `api-architect.agent.md` | Useful for the FastAPI layer, API contract cleanup, SSE design, and backend/frontend boundary review. |
| `polyglot-test-generator.agent.md` | Good match for increasing coverage and adding regression tests around Python behavior. |
| `adr-generator.agent.md` | Helps capture architecture decisions for model routing, caching rules, and read-only portfolio constraints. |
| `agent-governance-reviewer.agent.md` | Relevant because this is an agentic system in a high-trust domain and needs explicit governance and auditability. |
| `prompt-builder.agent.md` | Helpful when refining analyst prompts, output contracts, and system-message structure for safer report generation. |

Install a narrow set first. `api-architect.agent.md` and `agent-governance-reviewer.agent.md` are the strongest initial candidates, followed by `polyglot-test-generator.agent.md`.

## Guidance Hygiene

### README and AGENTS drift

- `README.md` previously listed only a subset of installed repo-local skills.
- `AGENTS.md` previously omitted `shadcn-component-discovery` from the skill registry.
- The repo should keep README, AGENTS, and actual `.github/skills/` contents synchronized.

### Suggested next repo-doc additions

- Add path-specific instruction files under `.github/instructions/` once review guidance needs to differ by area
- Add ADRs for cache invalidation policy, report verification, and API streaming contracts once those decisions are made
