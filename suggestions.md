# Suggestions for Artha

This file tracks practical improvement recommendations for Artha from a senior GenAI architect perspective. The emphasis is on auditability, regression resistance, conservative financial behavior, and keeping repository guidance aligned with the actual codebase.

## Highest Priority

### 1. Add eval-driven quality gates for report behavior

Relevant skills: `eval-driven-dev`, `agentic-eval`, `pytest-coverage`

Artha has broad unit coverage, but it still lacks a durable evaluation layer for the LLM-facing parts of the system. Add a `pixie_qa/` or equivalent eval folder with:

- saved portfolio snapshots and company-analysis fixtures
- expected output envelopes for `PortfolioReport`
- regression checks for verdict distribution, rebalance reasoning, and error fallback behavior
- prompt-change tests that fail when reports drift outside acceptable bounds

This should become the main guardrail for changes to prompts, tool schemas, orchestration, and parsing.

### 2. Add deterministic verification for financial claims

Relevant skill: `doublecheck`

The system makes high-stakes financial-analysis statements. Before presenting or persisting final summaries, introduce a verification layer for:

- price-derived calculations
- position weights and rebalance math
- source freshness
- unsupported claims in the free-text summary

The point is not to make the prose prettier; it is to reduce incorrect factual or numeric statements in a production-adjacent finance workflow.

### 3. Raise the CI and review bar further

Relevant skills: `dependabot`, `codeql`, `secret-scanning`, `gh-cli`

The baseline repo automation now exists. The next practical additions are:

- add a fast linter job, preferably `ruff`, to `/.github/workflows/ci.yml`
- make branch protection require `CI`, `CodeQL`, and one human approval
- enable repository secret scanning and push protection in GitHub settings
- install and tune CodeRabbit on the public repo now that public-repo reviews are free
- add path-specific Copilot instructions under `/.github/instructions/` if review guidance becomes too broad

This will do more for long-term quality than adding more prompt text.

## Medium Priority

### 4. Move API report listing off full-file reparsing

Relevant files: `api/main.py`, `snapshot_store.py`

`GET /api/reports` currently rebuilds summary metadata by opening and validating each report file. Add a sidecar index such as `reports/index.json` or an append-only report ledger so the dashboard can load histories without reparsing the full corpus on every request.

### 5. Make run progress a structured interface

Relevant files: `main.py`, `orchestrator.py`, `api/main.py`

The API streams CLI output and parses progress from console text. Replace that with structured progress events emitted by the orchestrator so the FastAPI layer can stream typed states instead of depending on terminal formatting.

### 6. Improve auth/session reliability around Kite MCP

Relevant files: `main.py`, `kite_runtime.py`, `tools.py`

There is still a risk boundary between authenticating one session and running analysis through another. Tighten session reuse or make the handoff explicit so `kite-login` and `run` do not behave like loosely coupled flows.

### 7. Make cached analysis refresh more event-aware

Relevant files: `analyst.py`, `orchestrator.py`, `config.py`

The current cache TTL is time-based. Consider invalidating company artifacts earlier when:

- earnings dates pass
- large price moves occur
- new filings arrive
- source URLs have gone stale

Time-only freshness is simple, but it is not enough for a finance workflow.

## Lower Priority

### 8. Formalize the MF API contract

Relevant files: `api/main.py`, `models.py`

The holdings endpoint currently mixes equity holdings with the latest saved MF snapshot. A dedicated `/api/mf-holdings` endpoint or a shared typed response contract would make the frontend boundary cleaner.

### 9. Add better run manifests and evidence logs

Relevant files: `snapshot_store.py`, `usage_tracking.py`, `models.py`

Persist a per-run manifest with:

- snapshot paths used
- analyst inputs and elapsed times
- price-history payload versions
- verdict counts and failure reasons

Also consider an evidence trail per verdict rather than just final source URLs.

### 10. Factor service-layer modules more explicitly

Relevant files: `orchestrator.py`, `analyst.py`, `research.py`, `kite_runtime.py`

These files already form a service layer. If the codebase grows, move them under a dedicated package boundary so orchestration, tool execution, and persistence responsibilities stay clear.

## Copilot Improvements

### Current state

- Repo-local skills exist under `.github/skills/`
- Repo-local custom agents exist under `.github/agents/`
- Repository-wide Copilot instructions exist at `.github/copilot-instructions.md`
- There is no `.github/instructions/` directory

### Recommended custom instructions to add

The latest `awesome-copilot` instruction catalog includes several assets that fit this repo well:

| Instruction | Why it fits Artha |
| --- | --- |
| `code-review-generic.instructions.md` | Useful default review mode for a repo where correctness, regressions, and test impact matter more than style churn. |
| `github-actions-ci-cd-best-practices.instructions.md` | Fits the current repo gap around CI, CodeQL, and repository automation. |
| `python-mcp-server.instructions.md` | Relevant because Artha depends heavily on MCP integration and may eventually grow local MCP tooling. |
| `ai-prompt-engineering-safety-best-practices.instructions.md` | Good fit for an LLM-driven financial-analysis system where safe prompting and claim discipline matter. |

Do not add framework-specific instructions that do not match this repo. There is no strong need here for .NET, Java, or frontend-only instruction packs.

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
