from __future__ import annotations

import json

from analysis.fiscal import get_fiscal_context
from models import AnalystInputPayload, Holding


def build_analyst_user_prompt(
    *,
    holding: Holding,
    analyst_input: AnalystInputPayload,
    data_card_sections: dict,
    max_searches: int,
    retry_context: str | None = None,
) -> str:
    fiscal = get_fiscal_context()
    user_prompt = (
        f"Analyse this Indian stock. Today: {fiscal['today_date']}. "
        f"Latest published quarter: {fiscal['latest_quarter']}. "
        f"Current period: {fiscal['current_quarter']}.\n\n"
        f"Run exactly {max_searches} tavily_search calls in this order:\n"
        f"  1. '{holding.tradingsymbol} {fiscal['latest_quarter']} quarterly results management commentary guidance'\n"
        f"  2. '{holding.tradingsymbol} management quality competitive moat market share {fiscal['current_fy']}'\n"
        f"  3. '{holding.tradingsymbol} risks regulatory sector outlook {fiscal['current_fy']}'\n"
        f"  4. '{holding.tradingsymbol} analyst target price consensus rating {fiscal['current_fy']}'\n\n"
        "Capture the exact URL from every search result you use — add them all to data_sources.\n"
        "Fill all 12 source_map keys: revenue_cagr, eps_cagr, roce, roe, pe, peg, fcf_yield, debt_to_equity, fair_value, risk_1, analyst_target, market_share.\n"
        "source_map values MUST be URLs (https://...) or 'Not available'. NEVER put data values in source_map.\n"
        "NEVER cite a 5-year or 3-year historical CAGR. Use only the latest 1-2 quarters YoY trend.\n"
        "eps_cagr MUST be per-share EPS, NOT absolute net profit in crores.\n"
        "Return exactly one valid JSON object. No markdown fences. No text outside the JSON.\n\n"
        "## Pre-Computed Data Card (use these FACTS, do not recompute):\n"
        + json.dumps(data_card_sections, indent=2, default=str)
        + "\n\n## Portfolio Input:\n"
        + analyst_input.model_dump_json(by_alias=True)
    )
    injected_block = _build_injected_values_block(data_card_sections=data_card_sections, fiscal=fiscal)
    if injected_block:
        user_prompt += injected_block
    if retry_context:
        return retry_context + "\n\n" + user_prompt
    return user_prompt


def build_yfinance_only_messages(
    *,
    holding: Holding,
    raw_company_info: dict,
    yfinance_data: dict,
) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": (
                "Convert this Yahoo Finance company data into AnalystReportCard JSON. "
                "Use direct numeric fields exactly when present. "
                "Keep the verdict conservative. If evidence is incomplete, prefer HOLD and LOW confidence.\n\n"
                f"Ticker: {holding.tradingsymbol}\n"
                f"Flat snapshot:\n{json.dumps(yfinance_data, ensure_ascii=True)}\n\n"
                f"Raw Yahoo Finance JSON:\n{json.dumps(raw_company_info, ensure_ascii=True)}"
            ),
        }
    ]


def _build_injected_values_block(*, data_card_sections: dict, fiscal: dict[str, str]) -> str:
    valuation = data_card_sections.get("valuation", {})
    quality = data_card_sections.get("quality", {})
    financials = data_card_sections.get("financials", {})
    quarterly = data_card_sections.get("nse_quarterly", {})
    price_data = data_card_sections.get("price_data", {})
    injected_values: list[str] = []
    if valuation.get("trailing_pe"):
        injected_values.append(f"- Trailing PE: {valuation['trailing_pe']:.1f}x (TTM, yfinance)")
    if valuation.get("sector_pe"):
        injected_values.append(f"- Sector PE: {valuation['sector_pe']:.1f}x (NSE India)")
    if valuation.get("pe_premium_to_sector_pct"):
        injected_values.append(f"- PE vs sector: {valuation['pe_premium_to_sector_pct']:+.1f}%")
    if quality.get("roe_proxy_pct"):
        injected_values.append(f"- ROE: {quality['roe_proxy_pct']:.1f}% (yfinance)")
    if quality.get("roce_proxy_pct"):
        injected_values.append(f"- ROCE: {quality['roce_proxy_pct']:.1f}% (yfinance)")
    if financials.get("debt_to_equity") is not None:
        injected_values.append(f"- Debt/Equity: {financials['debt_to_equity']:.2f}x (yfinance)")
    if quarterly.get("revenue_yoy_pct"):
        injected_values.append(
            f"- Revenue YoY ({fiscal['latest_quarter']}): {quarterly['revenue_yoy_pct']:.1f}% (NSE India)"
        )
    if quarterly.get("revenue_qoq_pct"):
        injected_values.append(f"- Revenue QoQ: {quarterly['revenue_qoq_pct']:.1f}% (NSE India)")
    if price_data.get("vs_200dma_pct"):
        injected_values.append(f"- Price vs 200 DMA: {price_data['vs_200dma_pct']:.1f}% (yfinance)")
    if price_data.get("alpha_vs_nifty_52w_pct"):
        injected_values.append(f"- Alpha vs Nifty 52w: {price_data['alpha_vs_nifty_52w_pct']:.1f}% (yfinance)")
    if quality.get("delivery_pct"):
        injected_values.append(f"- Delivery %: {quality['delivery_pct']:.1f}% (NSE India)")
    if not injected_values:
        return ""
    return (
        "\n\n## PRE-COMPUTED FACTS — USE THESE EXACT VALUES IN YOUR REPORT CARD\n"
        "Do NOT compute your own versions. Copy these directly:\n"
        + "\n".join(injected_values)
        + "\n\nUse these exact values in your report card to ensure internal consistency.\n"
    )
