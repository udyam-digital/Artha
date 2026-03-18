from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from config import Settings, get_settings
from models import (
    EquityResearchArtifact,
    Holding,
    MFHolding,
    MFResearchArtifact,
    MFSnapshot,
    PortfolioSnapshot,
    ResearchDigest,
)
from snapshot_store import load_latest_mf_snapshot, load_latest_portfolio_snapshot, save_research_digest
from tools import get_web_search_tool_definition


logger = logging.getLogger(__name__)


class ResearchExecutionError(RuntimeError):
    pass


class DeepResearchOrchestrator:
    def __init__(self, settings: Settings | None = None, client: AsyncAnthropic | None = None):
        self.settings = settings or get_settings()
        self.client = client or AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        self.equity_framework = (Path("skills") / "equity_analysis.md").read_text(encoding="utf-8")
        self.portfolio_rules = (Path("skills") / "portfolio_rules.md").read_text(encoding="utf-8")
        self.web_search_tool = get_web_search_tool_definition()

    async def research_latest_snapshots(self) -> tuple[ResearchDigest, Path, list[Path], Path]:
        portfolio_snapshot = load_latest_portfolio_snapshot(self.settings)
        mf_snapshot = self._load_latest_mf_snapshot_optional()
        return await self.research_snapshots(portfolio_snapshot, mf_snapshot)

    async def research_snapshots(
        self,
        portfolio_snapshot: PortfolioSnapshot,
        mf_snapshot: MFSnapshot | None,
    ) -> tuple[ResearchDigest, Path, list[Path], Path]:
        equity_tasks = [
            self._research_equity_holding(holding)
            for holding in portfolio_snapshot.holdings
            if holding.tradingsymbol not in {"LIQUIDBEES", "NIFTYBEES", "GOLDCASE", "SILVERCASE"}
        ]
        mf_tasks = [self._research_mf_holding(holding) for holding in (mf_snapshot.holdings if mf_snapshot else [])]

        equity_results = await asyncio.gather(*equity_tasks, return_exceptions=True)
        mf_results = await asyncio.gather(*mf_tasks, return_exceptions=True)

        equity_reports: list[EquityResearchArtifact] = []
        mf_reports: list[MFResearchArtifact] = []
        errors: list[str] = []
        per_holding_payloads: dict[str, dict[str, Any]] = {}

        for result in equity_results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue
            equity_reports.append(result)
            per_holding_payloads[result.identifier] = result.model_dump(mode="json")

        for result in mf_results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue
            mf_reports.append(result)
            per_holding_payloads[result.identifier] = result.model_dump(mode="json")

        digest_text = await self._build_digest_text(equity_reports, mf_reports, errors)
        digest = ResearchDigest(
            generated_at=datetime.now(timezone.utc),
            equity_reports=equity_reports,
            mf_reports=mf_reports,
            portfolio_digest=digest_text,
            errors=errors,
        )
        digest_path, holding_paths, index_path = save_research_digest(
            digest,
            per_holding_payloads,
            settings=self.settings,
        )
        return digest, digest_path, holding_paths, index_path

    def _load_latest_mf_snapshot_optional(self) -> MFSnapshot | None:
        path = self.settings.kite_data_dir / "mf" / "latest_snapshot.json"
        if not path.exists():
            return None
        return load_latest_mf_snapshot(self.settings)

    async def _research_equity_holding(self, holding: Holding) -> EquityResearchArtifact:
        system = (
            f"{self.equity_framework}\n\n{self.portfolio_rules}\n\n"
            "You are a dedicated Artha sub-agent researching exactly one Indian equity holding. "
            "Keep searching until you have enough current evidence to produce a confident, source-cited report."
        )
        user_prompt = (
            f"Research this Indian equity holding deeply: {holding.tradingsymbol} on {holding.exchange}. "
            f"Current portfolio weight: {holding.current_weight_pct:.2f}%. "
            f"Target weight: {holding.target_weight_pct:.2f}%. "
            "Use web_search extensively across Screener, latest results, concalls, investor presentations, "
            "exchange filings, and recent sector/company news. "
            "Return exactly one JSON object wrapped in <equity_research>...</equity_research> tags with keys: "
            "identifier, title, data_freshness, sources, bull_case, bear_case, what_to_watch, red_flags, confidence_summary."
        )
        raw_text = await self._run_tool_loop(system=system, user_prompt=user_prompt)
        payload = self._extract_tagged_json(raw_text, "equity_research", holding.tradingsymbol)
        report = EquityResearchArtifact(
            generated_at=datetime.now(timezone.utc),
            identifier=str(payload.get("identifier", holding.tradingsymbol)).upper(),
            title=str(payload.get("title", holding.tradingsymbol)),
            data_freshness=str(payload.get("data_freshness", "Unknown")),
            sources=self._coerce_string_list(payload.get("sources")),
            bull_case=str(payload.get("bull_case", "")),
            bear_case=str(payload.get("bear_case", "")),
            what_to_watch=str(payload.get("what_to_watch", "")),
            red_flags=self._coerce_string_list(payload.get("red_flags")),
            confidence_summary=str(payload.get("confidence_summary", "")),
        )
        logger.info(
            "Completed equity research for %s with %s sources",
            report.identifier,
            len(report.sources),
        )
        return report

    async def _research_mf_holding(self, holding: MFHolding) -> MFResearchArtifact:
        system = (
            "You are a dedicated Artha sub-agent researching exactly one Indian mutual fund holding. "
            "Keep searching until you have enough current evidence to produce a confident, source-cited report. "
            "Focus on category, mandate, style, concentration, expense ratio, AUM, overlap risk, and recent fund commentary. "
            "Do not analyze a mutual fund like a single stock."
        )
        user_prompt = (
            f"Research this Indian mutual fund deeply: {holding.fund}. "
            f"Scheme type: {holding.scheme_type or 'Unknown'}. Plan: {holding.plan or 'Unknown'}. "
            f"Current value: INR {holding.current_value:.2f}. "
            "Use web_search extensively across AMC pages, Value Research, Morningstar if available, factsheets, "
            "portfolio disclosures, and recent fund commentary. "
            "Return exactly one JSON object wrapped in <mf_research>...</mf_research> tags with keys: "
            "identifier, title, data_freshness, sources, fund_house, category, mandate, portfolio_style, "
            "expense_ratio_note, aum_note, overlap_risk, recent_commentary, risks, confidence_summary."
        )
        raw_text = await self._run_tool_loop(system=system, user_prompt=user_prompt)
        payload = self._extract_tagged_json(raw_text, "mf_research", holding.fund)
        report = MFResearchArtifact(
            generated_at=datetime.now(timezone.utc),
            identifier=str(payload.get("identifier", holding.tradingsymbol or holding.fund)),
            title=str(payload.get("title", holding.fund)),
            data_freshness=str(payload.get("data_freshness", "Unknown")),
            sources=self._coerce_string_list(payload.get("sources")),
            fund_house=str(payload.get("fund_house", "")),
            category=str(payload.get("category", "")),
            mandate=str(payload.get("mandate", "")),
            portfolio_style=str(payload.get("portfolio_style", "")),
            expense_ratio_note=str(payload.get("expense_ratio_note", "")),
            aum_note=str(payload.get("aum_note", "")),
            overlap_risk=str(payload.get("overlap_risk", "")),
            recent_commentary=str(payload.get("recent_commentary", "")),
            risks=self._coerce_string_list(payload.get("risks")),
            confidence_summary=str(payload.get("confidence_summary", "")),
        )
        logger.info(
            "Completed MF research for %s with %s sources",
            report.identifier,
            len(report.sources),
        )
        return report

    async def _run_tool_loop(self, *, system: str, user_prompt: str) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        for iteration in range(1, self.settings.max_iterations + 1):
            response = await self.client.messages.create(
                model=self.settings.model,
                max_tokens=self.settings.max_tokens,
                system=system,
                messages=messages,
                tools=[self.web_search_tool],
            )
            stop_reason = getattr(response, "stop_reason", None)
            if stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if stop_reason in {"end_turn", "max_tokens", "tool_use"}:
                return self._extract_text(response)
        raise ResearchExecutionError("Research sub-agent exceeded MAX_ITERATIONS.")

    async def _build_digest_text(
        self,
        equity_reports: list[EquityResearchArtifact],
        mf_reports: list[MFResearchArtifact],
        errors: list[str],
    ) -> str:
        prompt = {
            "equity_reports": [report.model_dump(mode="json") for report in equity_reports],
            "mf_reports": [report.model_dump(mode="json") for report in mf_reports],
            "errors": errors,
        }
        response = await self.client.messages.create(
            model=self.settings.model,
            max_tokens=min(self.settings.max_tokens, 2000),
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a concise portfolio research digest for an Indian investor using the supplied "
                        "holding reports. Cover major strengths, concentration risks, and what deserves follow-up. "
                        f"Input JSON:\n{json.dumps(prompt, ensure_ascii=True)}"
                    ),
                }
            ],
        )
        return self._extract_text(response) or "No research digest generated."

    def _extract_text(self, response: Any) -> str:
        text_parts: list[str] = []
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        return "\n".join(text_parts).strip()

    def _extract_tagged_json(self, raw_text: str, tag: str, identifier: str) -> dict[str, Any]:
        pattern = rf"<{tag}>\s*(\{{.*\}})\s*</{tag}>"
        match = re.search(pattern, raw_text, re.DOTALL)
        if not match:
            raise ResearchExecutionError(f"Research output for {identifier} did not contain <{tag}> JSON tags.")
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ResearchExecutionError(f"Research output for {identifier} was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ResearchExecutionError(f"Research output for {identifier} was not a JSON object.")
        return payload

    def _coerce_string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if value in (None, ""):
            return []
        return [str(value)]
