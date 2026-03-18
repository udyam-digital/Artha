# Suggestions

## Architecture

- Move the remaining live-Kite parsing helpers from `tools.py` into a dedicated `kite_parsing.py` module. `tools.py` still owns transport, auth helpers, payload normalization, and Anthropic tool definitions, which is broader than one module should carry.
- Keep `agent.py` focused on portfolio-level report generation and resist adding more orchestration there. The new `research.py` should stay the only place that manages per-holding deep-research workers.
- Consider adding a `service/` package if Artha grows further. Right now `kite_runtime.py`, `research.py`, and `snapshot_store.py` are enough, but a package boundary will become useful if reporting or tax-aware workflows expand.

## Reliability

- Add a lightweight snapshot manifest that records the exact auth artifact, sync timestamp, equity snapshot path, and MF snapshot path used for each generated report. That will improve auditability.
- Add retry/backoff around Anthropic requests in both `agent.py` and `research.py`. Network or rate-limit failures should degrade to partial output rather than fail an entire long run.
- Add a small integration smoke test layer behind an opt-in env flag so hosted Kite MCP and Anthropic connectivity can be validated without changing unit-test purity.

## Research Quality

- Add a dedicated `skills/mf_analysis.md` file once the MF workflow stabilizes. Keeping fund-research guidance in a markdown skill, instead of inline prompt text, will make behavior easier to evolve and review.
- Add source-domain heuristics for fund research so AMC factsheets, scheme pages, and fund-disclosure documents are prioritized over low-signal aggregator pages.
- For equity research, consider storing a short structured evidence log per holding, not just the final artifact. It will make Artha’s conclusions easier to inspect when recommendations change over time.
