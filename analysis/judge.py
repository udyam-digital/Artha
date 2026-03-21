from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from analysis.fiscal import get_fiscal_context
from config import Settings


logger = logging.getLogger(__name__)


def _build_rubric() -> str:
    ctx = get_fiscal_context()
    latest_q = ctx["latest_quarter"]   # e.g. "Q3 FY26"
    prev_q = ctx["prev_quarter"]       # e.g. "Q2 FY26"
    current_fy = ctx["current_fy"]     # e.g. "FY26"
    return f"""
You are a senior equity research QA evaluator for Indian stocks.

Today: {ctx['today_date']}. Latest published quarter: {latest_q}.

Score the following analyst report card JSON on four dimensions.
Return ONLY valid JSON — no markdown fences, no explanation outside the JSON.

SCORING RUBRIC:

recency (0-100):
  - Does revenue_cagr or eps_cagr reference {latest_q} or {prev_q} YoY trend? → high score
  - Does it reference only annual FY figures, "3-year historical CAGR", or stale periods? → max 40
  - Is growth_score consistent with the recent quarterly profit trend shown in company_risks? → required

risk_completeness (0-100):
  - Are there 4+ risks total (2+ company, 1+ structural, 1+ cyclical)? → required for >60
  - Are risks full sentences with specific mechanisms? → high score
  - Generic 1-2 word labels (e.g. "competition", "market risk") → deduct 20 each
  - Does it mention sector-specific competitive or regulatory threats? → required for >80

valuation_accuracy (0-100):
  - Is PE or fair_value_range based on TTM/{current_fy} earnings, not peak-year? → required
  - Is fair_value_range consistent with sector_pe × growth_score logic? → check
  - Is margin_of_safety realistic vs current price vs fair value range? → check

verdict_logic (0-100):
  - Does the final verdict align with timing_signal and risk_level?
  - BUY/ADD + Risky timing + High risk = contradiction → max 40
  - Is confidence (HIGH/MEDIUM/LOW) justified by data quality and number of sources?
  - Fewer than 2 real URLs in data_sources → LOW confidence is required

Return this exact JSON:
{{
  "recency": 0,
  "risk_completeness": 0,
  "valuation_accuracy": 0,
  "verdict_logic": 0,
  "overall": 0,
  "key_issues": ["issue 1", "issue 2"],
  "one_line_summary": "plain English verdict on quality"
}}

Where overall = recency*0.35 + risk_completeness*0.25 + valuation_accuracy*0.20 + verdict_logic*0.20
"""


async def judge_report_card(
    report_card_json: str,
    ticker: str,
    config: Settings,
    client: AsyncAnthropic | Any,
) -> dict[str, Any] | None:
    """
    Asks Haiku to grade an analyst report card.
    Returns score dict or None on failure. Never raises.
    """
    raw_client: AsyncAnthropic = getattr(client, "client", client)
    rubric = _build_rubric()
    try:
        response = await raw_client.messages.create(
            model=config.analyst_model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": f"{rubric}\n\nREPORT CARD:\n{report_card_json}",
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


def _build_factual_rubric() -> str:
    ctx = get_fiscal_context()
    return f"""
You are a senior equity research fact-checker for Indian stocks.

Today: {ctx['today_date']}. Latest published quarter: {ctx['latest_quarter']}.

Evaluate the analyst report card JSON for factual grounding, hallucination risk,
and internal data consistency. Return ONLY valid JSON — no markdown fences, no explanation.

SCORING RUBRIC:

source_grounding (0-100):
  - Does source_map exist with 5+ entries mapping metric names (revenue_cagr, roce, pe, etc.) to URLs? → required for >80
  - Do source_map URLs match entries in data_sources? → required for >70
  - Are there 3+ real URLs in data_sources? → required for >50
  - Do URLs look like real Indian financial sites (screener.in, moneycontrol.com, trendlyne.com, tickertape.in, bseindia.com)? → high score
  - Each claimed number (ROCE, PE, revenue growth, EPS) should have a source_map entry pointing to a real URL → required for >90
  - Generic or placeholder URLs, or empty source_map → max 20

hallucination_risk (0-100, inverted — high = good, low = bad):
  - Are numerical claims (growth rates, PE ratios, fair values) plausible given the sector? → high score
  - Are there fabricated-looking specifics (exact quarterly numbers, competitor cost advantages) with no matching data_source or source_map entry? → deduct 30
  - Does revenue_cagr / eps_cagr look realistic for the sector and market cap? → check
  - Does eps_cagr reference per-share EPS (not absolute net profit in crores)? Confusing net profit with EPS → deduct 20
  - Is a "5-year CAGR" or "3-year CAGR" cited? Historical multi-year CAGR is stale data → deduct 15
  - Outlandish fair_value_range vs current_price → deduct 20
  - Claims about analyst targets, FII positions, or competitor data without a source_map entry → deduct 15 each
  - Claims of "market leader", "monopolistic position", or competitive superiority without sourced evidence → deduct 10

data_consistency (0-100):
  - Do growth_score, quality_score, rvs_score internally align with raw metrics (ROCE, PE, etc.)?
  - Does fair_value_range make sense vs current_price and PE? → check
  - Is margin_of_safety consistent with fair_value_range vs current_price? Recalculate: (midpoint - price)/price*100. If the sign or magnitude is wrong by >5pp → deduct 20
  - Does risk_level align with the risks listed? → check
  - Are source_map entries consistent with the actual field values they claim to source? → check
  - Action plan zone ordering: stop_loss < buy_zone < add_zone < trim_zone? Violations → deduct 15
  - For HOLD verdict: is add_zone above current_price while margin_of_safety is negative? → deduct 10 (incoherent)

Return this exact JSON:
{{
  "source_grounding": 0,
  "hallucination_risk": 0,
  "data_consistency": 0,
  "overall": 0,
  "red_flags": ["flag 1"],
  "one_line_summary": "plain English verdict on factual quality"
}}

Where overall = source_grounding*0.40 + hallucination_risk*0.35 + data_consistency*0.25
"""


async def judge_factual_grounding(
    report_card_json: str,
    ticker: str,
    config: Settings,
    client: AsyncAnthropic | Any,
) -> dict[str, Any] | None:
    """
    Asks Haiku to evaluate factual grounding, hallucination risk, and data consistency.
    Returns score dict or None on failure. Never raises.
    """
    raw_client: AsyncAnthropic = getattr(client, "client", client)
    rubric = _build_factual_rubric()
    try:
        response = await raw_client.messages.create(
            model=config.analyst_model,
            max_tokens=1536,
            messages=[
                {
                    "role": "user",
                    "content": f"{rubric}\n\nREPORT CARD:\n{report_card_json}",
                }
            ],
        )
        text: str = response.content[0].text.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        result: dict[str, Any] = json.loads(text)
        return result
    except Exception as exc:
        logger.warning("[%s] factual judge failed (non-fatal): %s", ticker, exc)
        return None
