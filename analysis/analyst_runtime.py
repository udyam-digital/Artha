from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from anthropic import AsyncAnthropic

from analysis.analyst_judging import (
    apply_data_card_overrides,
    build_judge_context,
    build_retry_context,
)
from analysis.analyst_judging import (
    combined_overall as compute_combined_overall,
)
from analysis.analyst_llm import (
    _coerce_report_card_with_instructor,
    _ensure_instructor_client,
    _log_input_tokens,
    _raw_client_for_counting,
)
from analysis.analyst_prompt import build_analyst_user_prompt
from analysis.artifact_builder import _build_company_data_card_artifact
from analysis.data_card import build_bulk_deals_section, build_company_data_card, build_recent_filings_section
from analysis.fallback import _build_fallback_verdict, _report_card_to_stock_verdict
from analysis.judge import judge_factual_grounding, judge_report_card
from analysis.tool_router import (
    _extract_text,
    _log_response_usage,
    _materialize_tool_results,
    _serialize_content_blocks,
)
from config import Settings
from kite.tools import (
    get_nse_india_provider_payload,
    get_yfinance_company_info,
    map_yfinance_snapshot,
)
from models import AnalystInputPayload, CompanyDataCard, Holding, StockVerdict
from observability.langfuse_client import get_langfuse, score_active_trace
from observability.usage import estimate_input_tokens
from persistence.store import save_company_analysis_artifact, save_judge_scores
from providers.nse_bse import get_bulk_deals, get_corporate_announcements
from providers.tavily import get_tavily_search_tool_definition

logger = logging.getLogger(__name__)
MAX_ANALYST_ITERATIONS = 6


def _build_analyst_input(
    *,
    holding: Holding,
    price_context: dict[str, float | str],
    yfinance_data: dict[str, object],
) -> AnalystInputPayload:
    return AnalystInputPayload(
        tradingsymbol=holding.tradingsymbol,
        exchange=holding.exchange,
        quantity=holding.quantity,
        average_price=holding.average_price,
        last_price=holding.last_price,
        pnl=holding.pnl,
        pnl_pct=holding.pnl_pct,
        current_weight_pct=holding.current_weight_pct,
        target_weight_pct=holding.target_weight_pct,
        drift=round(holding.current_weight_pct - holding.target_weight_pct, 3),
        **{
            "52w_high": price_context.get("52w_high", 0.0),
            "52w_low": price_context.get("52w_low", 0.0),
            "current_vs_52w_high_pct": price_context.get("current_vs_52w_high_pct", 0.0),
        },
        yfinance_data=yfinance_data,
    )


async def _fetch_company_inputs(
    *,
    holding: Holding,
    price_context: dict[str, float | str],
    config: Settings,
) -> tuple[dict[str, object], dict[str, object]]:
    yf_raw, nse_provider, announcements_raw, bulk_deals_raw = await asyncio.gather(
        get_yfinance_company_info(holding.tradingsymbol),
        get_nse_india_provider_payload(holding.tradingsymbol),
        get_corporate_announcements(holding.tradingsymbol, settings=config),
        get_bulk_deals(holding.tradingsymbol, settings=config),
    )
    yf_raw = yf_raw or {}
    nse_raw = nse_provider.get("raw", {}) if isinstance(nse_provider, dict) else {}
    ticker_norm = holding.tradingsymbol if holding.tradingsymbol.endswith(".NS") else f"{holding.tradingsymbol}.NS"
    yfinance_data = map_yfinance_snapshot(ticker_norm, yf_raw) if yf_raw else {}
    data_card_sections = build_company_data_card(
        ticker=holding.tradingsymbol,
        exchange=holding.exchange,
        yf_raw=yf_raw,
        nse_raw=nse_raw,
        price_context=price_context,
    )
    data_card_sections["recent_filings"] = build_recent_filings_section(announcements_raw)
    data_card_sections["bulk_deals"] = build_bulk_deals_section(holding.tradingsymbol, bulk_deals_raw)
    return yfinance_data, data_card_sections


try:
    from langfuse import observe as _lf_observe

    def _analyst_observe(fn: Any) -> Any:
        return _lf_observe(fn, as_type="generation", capture_input=False, capture_output=False)
except ImportError:

    def _analyst_observe(fn: Any) -> Any:  # type: ignore[misc]
        return fn


@_analyst_observe
async def generate_company_artifact(
    holding: Holding,
    price_context: dict[str, float | str],
    skills_content: str,
    client: AsyncAnthropic | Any,
    config: Settings,
    _retries_remaining: int | None = None,
    _retry_context: str | None = None,
) -> CompanyDataCard:
    started = time.perf_counter()
    logger.info("[%s] starting analysis", holding.tradingsymbol)
    lf = get_langfuse(config)
    if lf:
        try:
            lf.update_current_generation(
                name=f"analyst-{holding.tradingsymbol}",
                model=config.analyst_model,
                input={
                    "tradingsymbol": holding.tradingsymbol,
                    "exchange": holding.exchange,
                    "last_price": holding.last_price,
                    "pnl_pct": holding.pnl_pct,
                },
                metadata={"ticker": holding.tradingsymbol},
            )
        except Exception:
            pass

    instructor_client = _ensure_instructor_client(client, api_key=config.anthropic_api_key)
    raw_client = _raw_client_for_counting(instructor_client)
    tools = [get_tavily_search_tool_definition(config)]
    yfinance_data, data_card_sections = await _fetch_company_inputs(
        holding=holding,
        price_context=price_context,
        config=config,
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": build_analyst_user_prompt(
                holding=holding,
                analyst_input=_build_analyst_input(
                    holding=holding, price_context=price_context, yfinance_data=yfinance_data
                ),
                data_card_sections=data_card_sections,
                max_searches=config.analyst_max_searches,
                retry_context=_retry_context,
            ),
        }
    ]
    logger.info(
        "[%s] analyst prompt token estimate: ~%s",
        holding.tradingsymbol,
        estimate_input_tokens(messages=messages, system=skills_content),
    )
    searches_used = 0
    all_collected_urls: list[str] = []
    for iteration in range(1, MAX_ANALYST_ITERATIONS + 1):
        await _log_input_tokens(
            label=f"[{holding.tradingsymbol}]",
            client=instructor_client,
            model=config.analyst_model,
            messages=messages,
            system=skills_content,
            tools=tools,
        )
        call_kwargs = {
            "model": config.analyst_model,
            "max_tokens": config.analyst_max_tokens,
            "system": skills_content,
            "messages": messages,
            "tools": tools,
        }
        if searches_used == 0:
            call_kwargs["tool_choice"] = {"type": "any"}
        response = await raw_client.messages.create(**call_kwargs)
        _log_response_usage(
            label=f"[{holding.tradingsymbol}] analyst",
            model=config.analyst_model,
            response=response,
            settings=config,
            metadata={"phase": "analyst", "ticker": holding.tradingsymbol, "iteration": iteration},
        )
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": _serialize_content_blocks(response.content)})
            continue
        if stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": _serialize_content_blocks(response.content)})
            tool_results, search_increment, new_urls = _materialize_tool_results(
                response,
                config=config,
                search_budget_remaining=max(config.analyst_max_searches - searches_used, 0),
            )
            searches_used += search_increment
            all_collected_urls.extend(new_urls)
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue
        if stop_reason not in {"end_turn", "max_tokens"}:
            raise ValueError(f"Unexpected stop_reason: {stop_reason}")

        report_card, structured_response = await _coerce_report_card_with_instructor(
            instructor_client=instructor_client,
            config=config,
            holding=holding,
            raw_text=_extract_text(response),
        )
        report_card, overwritten_fields = apply_data_card_overrides(
            report_card=report_card,
            data_card_sections=data_card_sections,
            holding=holding,
            collected_urls=all_collected_urls,
        )
        _log_response_usage(
            label=f"[{holding.tradingsymbol}] analyst_structured",
            model=config.analyst_model,
            response=structured_response,
            settings=config,
            metadata={"phase": "analyst_structured", "ticker": holding.tradingsymbol, "iteration": iteration},
        )
        artifact = _build_company_data_card_artifact(
            report_card=report_card,
            holding=holding,
            config=config,
            data_card_sections=data_card_sections,
        )
        output_path = save_company_analysis_artifact(artifact, settings=config)
        data_card_context, overwritten_fields_context = build_judge_context(data_card_sections, overwritten_fields)
        report_card_json = artifact.analysis.model_dump_json()
        quality_scores = await judge_report_card(
            report_card_json=report_card_json,
            ticker=holding.tradingsymbol,
            config=config,
            client=raw_client,
            data_card_context=data_card_context,
            overwritten_fields_context=overwritten_fields_context,
        )
        factual_scores = await judge_factual_grounding(
            report_card_json=report_card_json,
            ticker=holding.tradingsymbol,
            config=config,
            client=raw_client,
            data_card_context=data_card_context,
            overwritten_fields_context=overwritten_fields_context,
        )
        combined_overall = compute_combined_overall(quality_scores, factual_scores)
        passed = combined_overall >= config.judge_retry_threshold
        save_judge_scores(
            ticker=holding.tradingsymbol,
            quality_scores=quality_scores,
            factual_scores=factual_scores,
            combined_overall=combined_overall,
            passed=passed,
            settings=config,
        )
        if lf:
            if quality_scores:
                score_active_trace(lf, quality_scores, holding.tradingsymbol, factual_scores)
            try:
                rc = artifact.analysis
                lf.update_current_generation(
                    output={
                        "verdict": rc.final_verdict.verdict,
                        "confidence": rc.final_verdict.confidence,
                        "combined_overall": combined_overall,
                    },
                    metadata={
                        "ticker": holding.tradingsymbol,
                        "verdict": rc.final_verdict.verdict,
                        "iterations": iteration,
                    },
                )
            except Exception:
                pass
        retries = _retries_remaining if _retries_remaining is not None else config.judge_max_retries
        if not passed and retries > 0:
            logger.warning(
                "[%s] combined judge score %.1f < %d, retrying (%d left)",
                holding.tradingsymbol,
                combined_overall,
                config.judge_retry_threshold,
                retries,
            )
            output_path.unlink(missing_ok=True)
            return await generate_company_artifact(
                holding=holding,
                price_context=price_context,
                skills_content=skills_content,
                client=client,
                config=config,
                _retries_remaining=retries - 1,
                _retry_context=build_retry_context(
                    combined_score=combined_overall,
                    quality_scores=quality_scores,
                    factual_scores=factual_scores,
                    collected_urls=all_collected_urls,
                ),
            )
        if not passed:
            logger.warning(
                "[%s] combined judge score %.1f < %d after all retries; proceeding anyway",
                holding.tradingsymbol,
                combined_overall,
                config.judge_retry_threshold,
            )
        logger.info(
            "[%s] saved company analysis to %s in %.1fs",
            holding.tradingsymbol,
            output_path,
            time.perf_counter() - started,
        )
        return artifact
    raise ValueError("Analyst sub-agent exceeded MAX_ANALYST_ITERATIONS.")


async def analyse_stock(
    holding: Holding,
    portfolio_total_value: float,
    price_context: dict[str, float | str],
    skills_content: str,
    client: AsyncAnthropic | Any,
    config: Settings,
) -> StockVerdict:
    del portfolio_total_value
    started = time.perf_counter()
    try:
        artifact = await generate_company_artifact(
            holding=holding,
            price_context=price_context,
            skills_content=skills_content,
            client=client,
            config=config,
        )
        verdict = _report_card_to_stock_verdict(
            artifact=artifact,
            holding=holding,
            duration_seconds=time.perf_counter() - started,
        )
    except Exception as exc:
        verdict = _build_fallback_verdict(
            holding=holding,
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )
    logger.info("[%s] done in %.1fs — %s", holding.tradingsymbol, verdict.analysis_duration_seconds, verdict.verdict)
    return verdict
