# Suggestions

## Architecture

- Factor the live portfolio pipeline into a `services/` package next. `orchestrator.py`, `analyst.py`, `research.py`, and `kite_runtime.py` now form a clear application-service layer and will become easier to evolve if they share a package boundary.
- Introduce a small `HoldingContext` model for orchestrator-to-analyst handoff. That will make the boundary explicit for 52-week context, target-weight drift, and future additions like holding period or thesis notes.
- Add a dedicated `rebalance_merge.py` helper if verdict-to-action policy becomes more nuanced. The current merge logic is still compact, but it is now a business rule layer rather than a pure formatting concern.

## Reliability

- Add retry with jittered backoff around all Anthropic calls in `analyst.py`, `orchestrator.py`, and `research.py`. Parallel analyst fan-out increases exposure to transient rate limits.
- Persist a run manifest per portfolio analysis that captures the synced snapshot paths, per-holding price-context payloads, elapsed time, and final verdict count. That will materially improve auditability.
- Cache recent price-history context and recent analyst verdicts for a short TTL. That will reduce redundant Kite and Anthropic load during repeated runs on the same day.

## Observability

- Emit one structured log event per analyst completion with symbol, duration, verdict, confidence, action, and error state. That will make the parallel run easy to inspect in production-like environments.
- Track orchestrator-level counters for analyzed equities, excluded ETFs, MF holdings, analyst failures, and total synthesis time.
- Add a lightweight timing breakdown to the saved report payload or sidecar artifact so portfolio sync, price-context fetch, analyst fan-out, and final synthesis can be compared over time.

## Research Quality

- Add source-domain ranking for analyst sub-agents so exchange filings, investor presentations, earnings releases, and Screener evidence are preferred over generic market-news summaries.
- Persist an evidence log per `StockVerdict`, not just the final source URLs. A short structured trail of what changed the verdict will make recommendation drift much easier to audit.
- Add a dedicated `skills/mf_analysis.md` if MF verdicting is ever introduced, but keep MF analysis informational unless there is an explicit product decision to expand scope.
