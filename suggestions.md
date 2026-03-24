# Suggestions for Artha

Last refreshed: 2026-03-24

This file tracks practical recommendations for Artha from a senior GenAI architect perspective. The focus is the current repository state, not historical branch work. Priorities below are ordered by expected impact on auditability, correctness, and operator trust.

## Current posture

Artha already has the right core shape for a production-adjacent, read-only finance agent:

- thin CLI entrypoints in `main.py` and `cli/`
- package-oriented orchestration under `application/`
- deterministic rebalance logic in `rebalance.py`
- provider adapters split under `providers/` and `kite/`
- report indexing, manifests, and artifact persistence in `persistence/store.py`
- FastAPI transport in `api/main.py`
- an MCP server surface in `mcp_server.py`
- repo-local Copilot skills, agents, and path-specific instructions already installed under `.github/`

The main gaps are no longer "add the basics"; they are now mostly about contract hardening, documentation drift control, and stronger evidence trails.

## Highest priority

### 1. Add a docs drift guard

Relevant files: `README.md`, `AGENTS.md`, `suggestions.md`, `cli/parser.py`, `.github/workflows/*.yml`

Documentation has drifted more than once because commands, workflows, agents, and repo-local skills evolved faster than the docs. This should be caught automatically.

Recommended next step:

- add a small test or script that asserts documented CLI commands match `cli/parser.py`
- verify the agent and skill registries in `AGENTS.md` against `.github/agents/` and `.github/skills/`
- fail CI when those inventories or command lists drift

Why this matters:

- this repo depends heavily on human and agent guidance files
- stale guidance in a finance repo is an operational risk, not just a docs issue

### 2. Strengthen provider contract fixtures and smoke tests

Relevant files: `providers/`, `kite/`, `analysis/`, `tests/test_macro_context.py`, `tests/test_yfinance_tool.py`, `tests/test_tools.py`

The weakest part of the system is still upstream payload shape volatility. Artha already has solid unit coverage, but live-provider contract changes are the most likely source of silent degradation.

Recommended next step:

- add saved real-world payload fixtures for Kite, Yahoo Finance, NSE India, and MoSPI
- add one smoke test per provider that validates the exact fields Artha depends on
- annotate provider parsing code with the specific field names and fallback expectations each parser relies on

Why this matters:

- most regressions here will not look like crashes; they will look like incomplete or subtly wrong analysis

### 3. Upgrade MCP server verification from unit coverage to artifact-backed integration coverage

Relevant files: `mcp_server.py`, `application/reporting.py`, `persistence/store.py`, `tests/test_mcp_server.py`

The MCP server is now part of the public interface. Current unit coverage is useful, but the higher-value risk is schema drift between saved reports/artifacts and what the MCP tools expose.

Recommended next step:

- add integration-style tests using saved `PortfolioReport` and company artifact fixtures
- verify that report-list summaries, full report fetches, and artifact fetches all remain readable across schema evolution
- explicitly assert that no write-capable or order-placement tool is exposed

Why this matters:

- MCP is a trust boundary
- this repo must remain analysis-only even as interfaces expand

## Medium priority

### 4. Improve session resilience for long-running analyses

Relevant files: `kite/runtime.py`, `api/main.py`, `application/orchestrator.py`

Artha handles login and initial sync conservatively, but there is still a plausible failure mode if the Kite session expires during a longer run or while the API is streaming results.

Recommended next step:

- define one consistent mid-run session-expiry strategy
- either fail fast with a typed reconnect error or support a bounded re-auth/retry path around read-only calls

### 5. Add per-verdict evidence trails, not just final URLs

Relevant files: `models/`, `analysis/source_map.py`, `persistence/store.py`, `application/orchestrator.py`

Reports already include source URLs, manifests, and usage logs. The remaining gap is traceability from each verdict field back to the evidence used at generation time.

Recommended next step:

- persist per-verdict evidence metadata with source URL, access timestamp, and which section of the report card it informed
- keep it compact and machine-readable so later audit tools can consume it

### 6. Refresh ADR status and architecture guidance

Relevant files: `docs/adr/0001-package-oriented-application-structure.md`, `AGENTS.md`

`ADR-0001` captures the right direction, but parts of its implementation notes now describe follow-ups that have already happened.

Recommended next step:

- either update `ADR-0001` from `Proposed` to the right current status or add a superseding ADR
- record the current package boundary decisions as they exist today, not as migration targets from last week

## Lower priority

### 7. Add a background run model for the API if the dashboard becomes a primary surface

Relevant files: `api/main.py`, `application/events.py`, `application/orchestrator.py`

The current SSE approach is reasonable for a local or operator-driven dashboard. If the API becomes multi-user or long-lived, inline request-bound execution will become a bottleneck.

Recommended next step:

- move run execution behind a job abstraction only when the UI actually needs queued or concurrent runs
- keep the existing event schema so the frontend contract stays stable

### 8. Expand evaluation coverage for research outputs

Relevant files: `application/research_orchestrator.py`, `tests/test_research.py`, `tests/test_analyst_evals.py`

Portfolio report validation is stronger than research digest validation.

Recommended next step:

- add eval-style tests for research digest structure, search-budget compliance, and error reporting quality
- keep this separate from deterministic rebalance math tests

## Copilot and repo guidance

### What is already present

The repo-local guidance surface is materially better than before:

- custom agents already exist under `.github/agents/`: `adr-generator`, `agent-governance-reviewer`, `api-architect`, `context-architect`, `critical-thinking`, `doublecheck`
- repo-local skills already exist under `.github/skills/`
- repository-wide Copilot instructions exist at `.github/copilot-instructions.md`
- path-specific instructions exist under `.github/instructions/`

This means the next improvement is not "add more guidance files by default." The next improvement is keeping those files synchronized with the real codebase and using them consistently when changes touch prompts, governance, architecture, or MCP boundaries.

### Narrow additions worth considering later

Only consider adding more repo-local assets if a specific workflow starts hurting:

- a docs-validation or repo-hygiene skill, if guidance drift keeps recurring
- a fixture-curation/testing skill, if provider payload churn becomes the main source of regressions
- a release-notes or changelog hygiene skill, if external consumers start depending on API or MCP surface stability

Do not add broad new skill packs unless they solve a real recurring problem in this repo.

## Summary

The repo no longer needs foundational setup advice. It needs discipline around:

- keeping guidance files current
- hardening upstream provider contracts
- preserving MCP and finance-domain trust boundaries
- improving auditability of why a verdict was produced
