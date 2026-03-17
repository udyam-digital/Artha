from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from config import Settings, get_settings
from models import PortfolioReport, PortfolioSnapshot
from rebalance import calculate_rebalancing_actions
from tools import KiteMCPClient, execute_tool_call, get_tool_definitions, load_kite_server_definition

logger = logging.getLogger(__name__)


class ArthaAgent:
    def __init__(self, settings: Settings | None = None, client: AsyncAnthropic | None = None):
        self.settings = settings or get_settings()
        self.client = client or AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        self.system_prompt = self._build_system_prompt()
        self.tools = get_tool_definitions(self.settings)

    def _build_system_prompt(self) -> str:
        equity_analysis = Path("skills") / "equity_analysis.md"
        portfolio_rules = Path("skills") / "portfolio_rules.md"
        return f"{equity_analysis.read_text(encoding='utf-8')}\n\n{portfolio_rules.read_text(encoding='utf-8')}"

    def _build_user_prompt(
        self,
        ticker: str | None = None,
        console_filename: str | None = None,
    ) -> str:
        if ticker:
            return (
                f"Run a single-stock deep dive for {ticker.upper()} from the live Kite portfolio. "
                "Use kite_get_portfolio to find the holding and kite_get_price_history for price context. "
                "Research the company with web_search. Do not create meaningful rebalancing actions; return an empty "
                "or HOLD-only rebalancing_actions list. "
                "Return the final answer as JSON wrapped in <artha_report>...</artha_report> tags."
            )

        console_instruction = ""
        if console_filename:
            console_instruction = (
                f" The user supplied Zerodha Console export '{console_filename}'. Read it with read_console_export "
                "and use it for LTCG/STCG-aware sell recommendations."
            )

        return (
            "Analyze the live Indian equity portfolio from Kite. Start by calling kite_get_portfolio. "
            "Use kite_get_price_history for price context where helpful, and web_search for holdings research. "
            "Exclude LIQUIDBEES, NIFTYBEES, GOLDCASE, and SILVERCASE from equity rebalancing actions, but keep them "
            "in the portfolio snapshot and total value. Never include MF holdings in equity rebalancing actions."
            f"{console_instruction} "
            "You must return exactly one JSON object wrapped in <artha_report>...</artha_report> tags that validates "
            "against the PortfolioReport schema. If data is partial, still return valid JSON and record issues in errors."
        )

    def _extract_text(self, response: Any) -> str:
        text_parts: list[str] = []
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        return "\n".join(text_parts).strip()

    def _fallback_report(
        self,
        raw_text: str,
        snapshot: PortfolioSnapshot | None,
        errors: list[str],
    ) -> PortfolioReport:
        snapshot = snapshot or PortfolioSnapshot(
            fetched_at=datetime.now(UTC),
            total_value=0.0,
            available_cash=0.0,
            holdings=[],
        )
        actions = calculate_rebalancing_actions(
            holdings=snapshot.holdings,
            total_value=snapshot.total_value,
            available_cash=snapshot.available_cash,
        )
        return PortfolioReport(
            generated_at=datetime.now(UTC),
            portfolio_snapshot=snapshot,
            analyses=[],
            rebalancing_actions=actions,
            portfolio_summary=raw_text or "Artha could not parse a valid final JSON response.",
            total_buy_required=sum(a.rupee_amount for a in actions if a.action == "BUY"),
            total_sell_required=sum(a.rupee_amount for a in actions if a.action == "SELL"),
            errors=errors,
        )

    def _parse_final_output(
        self,
        raw_text: str,
        snapshot: PortfolioSnapshot | None,
        errors: list[str],
    ) -> PortfolioReport:
        match = re.search(r"<artha_report>\s*(\{.*\})\s*</artha_report>", raw_text, re.DOTALL)
        if not match:
            errors.append("Final response did not contain <artha_report> JSON tags.")
            return self._fallback_report(raw_text, snapshot, errors)

        payload = match.group(1)
        try:
            return PortfolioReport.model_validate_json(payload)
        except Exception:
            logger.exception("PortfolioReport validation failed for final output")
            errors.append("PortfolioReport validation failed; returning raw text fallback.")
            return self._fallback_report(raw_text, snapshot, errors)

    async def run(
        self,
        ticker: str | None = None,
        console_filename: str | None = None,
    ) -> PortfolioReport:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._build_user_prompt(ticker=ticker, console_filename=console_filename)}
        ]
        errors: list[str] = []
        latest_snapshot: PortfolioSnapshot | None = None

        async with KiteMCPClient(load_kite_server_definition(self.settings)) as kite_client:
            for iteration in range(1, self.settings.max_iterations + 1):
                logger.info("Agent iteration %s/%s", iteration, self.settings.max_iterations)
                response = await self.client.messages.create(
                    model=self.settings.model,
                    max_tokens=self.settings.max_tokens,
                    system=self.system_prompt,
                    messages=messages,
                    tools=self.tools,
                )

                stop_reason = getattr(response, "stop_reason", None)
                if stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": response.content})
                    continue

                if stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": response.content})
                    tool_results = []
                    for block in response.content:
                        if getattr(block, "type", None) != "tool_use":
                            continue
                        payload, is_error, snapshot = await execute_tool_call(
                            name=block.name,
                            tool_input=block.input,
                            kite_client=kite_client,
                            settings=self.settings,
                        )
                        if snapshot is not None:
                            latest_snapshot = snapshot
                        if is_error:
                            try:
                                parsed = json.loads(payload)
                                errors.append(f"{block.name}: {parsed.get('error', 'tool failed')}")
                            except json.JSONDecodeError:
                                errors.append(f"{block.name}: tool failed")
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": payload,
                                **({"is_error": True} if is_error else {}),
                            }
                        )
                    messages.append({"role": "user", "content": tool_results})
                    continue

                if stop_reason == "max_tokens":
                    logger.warning("Anthropic response hit max_tokens")
                    errors.append("Claude hit max_tokens before finishing the report.")
                    raw_text = self._extract_text(response)
                    return self._parse_final_output(raw_text, latest_snapshot, errors)

                if stop_reason == "end_turn":
                    raw_text = self._extract_text(response)
                    return self._parse_final_output(raw_text, latest_snapshot, errors)

                errors.append(f"Unexpected stop_reason: {stop_reason}")
                raw_text = self._extract_text(response)
                return self._fallback_report(raw_text, latest_snapshot, errors)

        errors.append("Agent exceeded MAX_ITERATIONS and returned a partial report.")
        return self._fallback_report("", latest_snapshot, errors)

