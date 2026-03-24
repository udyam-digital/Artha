from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from analysis.analyst_llm import _ensure_instructor_client, _log_input_tokens
from analysis.analyst_prompt import build_yfinance_only_messages
from analysis.artifact_builder import _build_company_data_card_artifact, _fix_internal_consistency
from analysis.data_card import build_company_data_card
from analysis.source_map import REQUIRED_SOURCE_MAP_KEYS
from analysis.tool_router import _log_response_usage
from config import Settings
from kite.tools import get_yfinance_company_info, get_yfinance_snapshot
from models import AnalystReportCard, CompanyDataCard, Holding
from persistence.store import save_company_analysis_artifact


async def generate_yfinance_only_company_artifact(
    holding: Holding,
    client: AsyncAnthropic | Any,
    config: Settings,
) -> CompanyDataCard:
    raw_company_info = await get_yfinance_company_info(holding.tradingsymbol)
    yfinance_data = await get_yfinance_snapshot(holding.tradingsymbol)
    if not raw_company_info or not yfinance_data:
        raise ValueError(f"Yahoo Finance data unavailable for {holding.tradingsymbol}")
    instructor_client = _ensure_instructor_client(client, api_key=config.anthropic_api_key)
    system_prompt = (
        "You convert Yahoo Finance company data into a valid AnalystReportCard JSON object. "
        "Use only the provided JSON. Do not browse, do not search, and do not invent unsupported facts. "
        "When a field is missing, use 'Not available' for strings, 0.0 for numeric arrays, and [] for lists. "
        "Set data_sources to [] and set all 12 source_map keys to 'Not available'."
    )
    messages = build_yfinance_only_messages(
        holding=holding,
        raw_company_info=raw_company_info,
        yfinance_data=yfinance_data,
    )
    await _log_input_tokens(
        label=f"[{holding.tradingsymbol}] [yfinance-only]",
        client=instructor_client,
        model=config.analyst_model,
        messages=messages,
        system=system_prompt,
    )
    if hasattr(instructor_client.messages, "create_with_completion"):
        report_card, completion = await instructor_client.messages.create_with_completion(
            response_model=AnalystReportCard,
            model=config.analyst_model,
            max_tokens=config.analyst_max_tokens,
            system=system_prompt,
            messages=messages,
        )
        _log_response_usage(
            label=f"[{holding.tradingsymbol}] analyst_yfinance_only",
            model=config.analyst_model,
            response=completion,
            settings=config,
            metadata={"phase": "analyst_yfinance_only", "ticker": holding.tradingsymbol},
        )
    else:
        report_card = await instructor_client.messages.create(
            response_model=AnalystReportCard,
            model=config.analyst_model,
            max_tokens=config.analyst_max_tokens,
            system=system_prompt,
            messages=messages,
        )
    report_card = _fix_internal_consistency(report_card)
    report_card.data_sources = []
    report_card.source_map = {key: "Not available" for key in REQUIRED_SOURCE_MAP_KEYS}
    report_card.final_verdict.confidence = "Low"
    artifact = _build_company_data_card_artifact(
        report_card=report_card,
        holding=holding,
        config=config,
        data_card_sections=build_company_data_card(
            ticker=holding.tradingsymbol,
            exchange=holding.exchange,
            yf_raw=raw_company_info,
            nse_raw={},
            price_context={},
        ),
    )
    save_company_analysis_artifact(artifact, settings=config)
    return artifact
