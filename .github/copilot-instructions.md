# Artha Copilot Instructions

Artha is production-adjacent financial analysis software. Optimize for correctness, auditability, and conservative behavior over cleverness.

Core rules:

- Preserve read-only portfolio behavior. Do not add order placement, auto-trading, or any execution path unless explicitly requested.
- Keep final report output valid against the existing `PortfolioReport` schema and preserve current fallbacks when the LLM or tool output is partial or malformed.
- Treat financial calculations, rebalancing math, cache freshness rules, and holdings filters as correctness-sensitive behavior. Prefer deterministic fixes over prompt-only changes.
- Use live Kite data only for holdings-based decisions. MF holdings are informational only and must stay out of equity rebalance actions.
- When prompts, tools, or orchestration change, prioritize regression protection in tests.
- Generated artifacts, local caches, coverage output, and runtime data should not be committed unless the change explicitly requires it.

Review focus:

- Behavioral regressions in `agent.py`, `tools.py`, `rebalance.py`, `research.py`, `kite/runtime.py`, and API endpoints.
- Missing tests for deterministic logic changes.
- Unsafe assumptions around malformed provider responses, auth/session boundaries, or stale cached artifacts.
- Any change that weakens conservative error handling or auditability.
