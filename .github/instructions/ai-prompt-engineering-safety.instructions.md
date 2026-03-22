---
applyTo: "skills/**,analysis/analyst.py,analysis/judge.py,application/agent.py"
---
# Prompt Engineering and Safety Guidelines

- All system prompts must end with an explicit constraint against trade execution.
- Analyst prompts must reference the current fiscal quarter (use get_fiscal_context() variables).
- Judge prompts must distinguish between LLM-generated and Python-overwritten fields to avoid penalising verified data.
- Never instruct the LLM to take live financial actions. Analysis and recommendations only.
- When changing prompts, update tests/test_analyst_evals.py with regression assertions.
