---
applyTo: "**"
---
# Code Review Guidelines

- Prioritise correctness, regressions, and test impact over style churn.
- Flag any change that touches financial math (rebalance.py, analysis/), report parsing (persistence/store.py, application/reporting.py), or tool schemas (kite/tools.py) with a comment explaining what the downstream impact could be.
- Never approve a PR that removes a fallback or degrades error handling without a tested replacement.
- Check that prompt changes in skills/ have corresponding test updates in tests/test_analyst_evals.py or tests/test_analyst.py.
