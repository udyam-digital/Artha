---
title: "ADR-0001: Adopt a package-oriented application structure with thin entrypoints"
status: "Proposed"
date: "2026-03-20"
authors: "Repo maintainers, AI coding agents"
tags: ["architecture", "structure", "python", "fastapi", "agent-system"]
supersedes: ""
superseded_by: ""
---

# Status

Proposed

# Context

Artha has grown from a CLI-oriented agent into a mixed CLI plus FastAPI application with LLM orchestration, Kite MCP integration, cached company-analysis artifacts, observability, and multiple persistence paths.

The current structure shows signs of an incomplete module migration:

- Real implementations already exist under package directories such as `analysis/`, `kite/`, `observability/`, and `search/`.
- Top-level compatibility modules such as `analyst.py`, `company_analysis.py`, `kite_runtime.py`, `tools.py`, `telemetry.py`, and `usage_tracking.py` still exist and are imported broadly.
- The API layer in `api/main.py` imports orchestration and persistence concerns directly from the top level.
- Core business concerns such as orchestration, report synthesis, rebalancing, persistence, search, and MCP access are separated only partially, which makes ownership and refactor boundaries harder to reason about.

This shape is still workable, but it creates ambiguity about where new code should go and encourages continued growth of wrapper-style modules.

Key forces affecting the decision:

- The repo is production-adjacent financial-analysis software and needs conservative, auditable changes.
- Deterministic policy logic such as rebalancing must remain easy to isolate from agent behavior.
- CLI and API entrypoints should not accumulate business logic.
- Existing imports should not be broken in one large move without a migration path.

# Decision

Adopt a package-oriented application structure with thin entrypoints and explicit boundaries between interfaces, application services, domain logic, and infrastructure.

The target direction is:

## Entry points

- `main.py`: CLI entrypoint only
- `api/main.py`: FastAPI transport layer only

Both entrypoints should delegate quickly into package-owned services and avoid accumulating workflow logic.

## Domain layer

Keep deterministic portfolio concepts and contracts in boundary-stable modules:

- `models.py` for shared contracts, or later `domain/models.py`
- `rebalance.py` for deterministic portfolio policy, or later `domain/rebalance.py`

Domain logic must remain independent from MCP, HTTP, Anthropic transport details, and CLI formatting.

## Application layer

Create or consolidate a dedicated service layer for orchestration and use-case flows:

- `orchestrator.py`
- `agent.py`
- `research.py`
- any future report-index or progress-stream services

These should eventually live under an application package such as `application/` or `services/`.

Responsibilities:

- portfolio run orchestration
- single-stock analysis orchestration
- research fan-out and aggregation
- progress event emission
- report synthesis coordination

## Infrastructure layer

Keep external systems and persistence behind explicit modules:

- `kite/` for MCP client, runtime, and Kite tool adapters
- `analysis/` for company-analysis generation and artifact-to-verdict translation
- `observability/` for telemetry and usage ledgers
- `search/` for Tavily integration
- `snapshot_store.py`, or later `infrastructure/persistence/`

Infrastructure code may depend on frameworks and providers. Domain logic should not.

## Migration rule

During migration, top-level wrapper modules may remain temporarily as compatibility shims, but:

- new implementation code should go into the package-owned module, not the shim
- wrappers should re-export only
- wrappers should be reduced over time instead of expanded

## API boundary rule

The FastAPI layer should consume application services through typed request and response contracts. It should not own business workflow logic, report indexing policy, or progress parsing rules that belong in the application layer.

# Consequences

## Positive

- clearer ownership of new code
- less confusion between real implementation modules and compatibility wrappers
- easier refactoring of CLI and API separately
- better isolation of deterministic finance logic from LLM- and MCP-driven code
- simpler test strategy by layer
- safer long-term evolution for report indexing, SSE progress, and additional interfaces

## Negative

- migration will require temporary duplication and shims
- imports will remain mixed for some time
- tests may need staged updates as modules move
- maintainers must be disciplined about not adding new logic to wrapper files

# Alternatives Considered

## ALT-001: Keep the current mixed top-level plus package structure

Rejected because it preserves ambiguity about module ownership and makes the current half-migration permanent.

## ALT-002: Do a full package rewrite immediately

Rejected because it is too risky for a production-adjacent financial-analysis system and would create unnecessary churn across imports, tests, and runtime behavior.

## ALT-003: Collapse everything back to top-level modules

Rejected because package boundaries already exist and are the right long-term direction; removing them would be a step backward.

# Implementation Notes

Recommended migration sequence:

1. Stop adding new logic to wrapper modules.
2. Treat existing package directories as the source of truth.
3. Move orchestration-oriented modules into an explicit application package.
4. Introduce typed service interfaces for API and CLI consumption.
5. Keep wrappers only as temporary import bridges until call sites are cleaned up.

Immediate follow-ups aligned with this ADR:

- move report-list indexing logic out of `api/main.py` into an application or persistence service
- move run-progress event construction into the orchestrator layer instead of parsing CLI text in the API layer
- define whether `models.py` remains top-level or becomes part of an explicit domain package

# Stakeholders

- repository maintainers
- developers working on CLI and FastAPI flows
- AI coding agents operating under `AGENTS.md` and `CLAUDE.md`
- users relying on auditable, read-only portfolio analysis
