from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from config import Settings


logger = logging.getLogger(__name__)

_RUBRIC = """
You are a senior equity research QA evaluator for Indian stocks.

Score the following analyst report card JSON on four dimensions.
Return ONLY valid JSON — no markdown fences, no explanation outside the JSON.

SCORING RUBRIC:

recency (0-100):
  - Does revenue_cagr or eps_cagr reference Q3 FY26 or recent quarters? → high score
  - Does it reference FY25, FY24, "3-year historical", or "past"? → max 40
  - Is growth_score consistent with recent quarterly profit trend shown in risks? → required

risk_completeness (0-100):
  - Are there 5+ specific, quantified risks across structural/cyclical/company categories? → high score
  - Are risks generic (1-2 words, e.g. "market risk", "competition")? → deduct 20 each
  - Does it mention sector-specific threats where relevant (e.g. NSDL IPO for CDSL)? → required

valuation_accuracy (0-100):
  - Is PE or fair_value_range based on TTM/recent earnings, not peak-year? → required
  - Is fair_value_range internally consistent with stated sector_pe and growth_score? → check
  - Is margin_of_safety realistic given current price vs fair value range? → check

verdict_logic (0-100):
  - Does final verdict align with timing_signal and risk_level?
  - BUY + Risky timing + High risk = contradiction → max 40
  - Is confidence level (HIGH/MEDIUM/LOW) justified by data quality and recency?

Return this exact JSON:
{
  "recency": <0-100>,
  "risk_completeness": <0-100>,
  "valuation_accuracy": <0-100>,
  "verdict_logic": <0-100>,
  "overall": <weighted: recency*0.35 + risk_completeness*0.25 + valuation_accuracy*0.20 + verdict_logic*0.20>,
  "key_issues": ["issue 1", "issue 2"],
  "one_line_summary": "plain English verdict on quality"
}
"""


async def judge_report_card(
    report_card_json: str,
    ticker: str,
    config: Settings,
    client: AsyncAnthropic | Any,
) -> dict[str, Any] | None:
    """
    Asks Sonnet to grade a Haiku-produced analyst report card.
    Returns score dict or None on failure. Never raises.
    Cost: ~1K tokens ≈ $0.003 per call.
    """
    raw_client: AsyncAnthropic = getattr(client, "client", client)
    try:
        response = await raw_client.messages.create(
            model=config.model,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": f"{_RUBRIC}\n\nREPORT CARD:\n{report_card_json}",
                }
            ],
        )
        text: str = response.content[0].text.strip()
        # Strip markdown fences if the model adds them despite instructions
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        result: dict[str, Any] = json.loads(text)
        return result
    except Exception as exc:
        logger.warning("[%s] LLM judge failed (non-fatal): %s", ticker, exc)
        return None
