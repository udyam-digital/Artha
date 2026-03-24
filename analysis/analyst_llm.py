from __future__ import annotations

import logging
from typing import Any

import instructor
from anthropic import AsyncAnthropic

from analysis.source_map import (
    _extract_data_sources_from_raw,
    _extract_source_map_from_raw,
    _normalize_source_map_keys,
)
from config import Settings
from models import AnalystReportCard, Holding
from observability.usage import count_input_tokens_exact, log_estimated_input_tokens

logger = logging.getLogger(__name__)


def _make_instructor_client(api_key: str) -> instructor.AsyncInstructor:
    return instructor.from_anthropic(
        AsyncAnthropic(api_key=api_key),
        mode=instructor.Mode.ANTHROPIC_JSON,
    )


def _ensure_instructor_client(client: Any, *, api_key: str) -> Any:
    messages = getattr(client, "messages", None)
    if messages is not None and hasattr(messages, "create_with_completion"):
        return client
    if isinstance(client, AsyncAnthropic):
        return instructor.from_anthropic(client, mode=instructor.Mode.ANTHROPIC_JSON)
    if messages is not None and hasattr(messages, "create"):
        return client
    return _make_instructor_client(api_key)


def _raw_client_for_counting(client: Any) -> AsyncAnthropic | Any:
    return getattr(client, "client", client)


async def _log_input_tokens(
    *,
    label: str,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> None:
    try:
        exact = await count_input_tokens_exact(
            client=_raw_client_for_counting(client),
            model=model,
            messages=messages,
            system=system,
            tools=tools,
        )
    except Exception as exc:
        logger.warning("%s exact token counting failed; falling back to estimate: %s", label, exc)
        log_estimated_input_tokens(label=label, messages=messages, system=system)
        return
    logger.info("%s exact input tokens: %s", label, exact)


async def _coerce_report_card_with_instructor(
    *,
    instructor_client: Any,
    config: Settings,
    holding: Holding,
    raw_text: str,
) -> tuple[AnalystReportCard, Any]:
    raw_source_map = _extract_source_map_from_raw(raw_text)
    raw_data_sources = _extract_data_sources_from_raw(raw_text)
    messages = [
        {
            "role": "user",
            "content": (
                "Convert the following stock analysis draft into a valid AnalystReportCard JSON object. "
                "Use only facts present in the draft. Do not add commentary outside the schema. "
                "IMPORTANT: Preserve all data_sources URLs and source_map entries from the draft exactly as written.\n"
                f"Ticker: {holding.tradingsymbol}\n"
                f"Draft:\n{raw_text}"
            ),
        }
    ]
    await _log_input_tokens(
        label=f"[{holding.tradingsymbol}] [structured]",
        client=instructor_client,
        model=config.analyst_model,
        messages=messages,
    )
    if not hasattr(instructor_client.messages, "create_with_completion"):
        response = await instructor_client.messages.create(
            response_model=AnalystReportCard,
            model=config.analyst_model,
            max_tokens=config.analyst_max_tokens,
            messages=messages,
        )
        raise ValueError(
            f"Instructor client did not return completion metadata for {holding.tradingsymbol}: {response}"
        )

    report_card, completion = await instructor_client.messages.create_with_completion(
        response_model=AnalystReportCard,
        model=config.analyst_model,
        max_tokens=config.analyst_max_tokens,
        messages=messages,
    )
    if raw_source_map:
        if not report_card.source_map:
            report_card.source_map = raw_source_map
            logger.info(
                "[%s] re-injected source_map (%d entries) from raw text", holding.tradingsymbol, len(raw_source_map)
            )
        else:
            for key, value in raw_source_map.items():
                if key not in report_card.source_map:
                    report_card.source_map[key] = value
    report_card.source_map = _normalize_source_map_keys(report_card.source_map)
    if not report_card.data_sources and raw_data_sources:
        report_card.data_sources = raw_data_sources
        logger.info(
            "[%s] re-injected data_sources (%d URLs) from raw text", holding.tradingsymbol, len(raw_data_sources)
        )
    elif raw_data_sources:
        existing = set(report_card.data_sources)
        for url in raw_data_sources:
            if url not in existing:
                report_card.data_sources.append(url)
                existing.add(url)
    return report_card, completion
