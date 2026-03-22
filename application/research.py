from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
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
from observability.usage import log_estimated_input_tokens, record_anthropic_usage
from persistence.store import load_latest_mf_snapshot, load_latest_portfolio_snapshot, save_research_digest
from search.tavily import DEFAULT_TAVILY_MAX_RESULTS, get_tavily_search_tool_definition, tavily_search

logger = logging.getLogger(__name__)
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class ResearchExecutionError(RuntimeError):
    pass


class DeepResearchOrchestrator:
    def __init__(self, settings: Settings | None = None, client: AsyncAnthropic | None = None):
        self.settings = settings or get_settings()
        self.client = client or AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        self.equity_framework = (SKILLS_DIR / "equity_analysis.md").read_text(encoding="utf-8")
        self.portfolio_rules = (SKILLS_DIR / "portfolio_rules.md").read_text(encoding="utf-8")
        self.search_tool = get_tavily_search_tool_definition(self.settings)

    async def research_latest_snapshots(self) -> tuple[ResearchDigest, Path, list[Path], Path]:
        portfolio_snapshot = load_latest_portfolio_snapshot(self.settings)
        mf_snapshot = self._load_latest_mf_snapshot_optional()
        return await self.research_snapshots(portfolio_snapshot, mf_snapshot)

    async def research_snapshots(
        self,
        portfolio_snapshot: PortfolioSnapshot,
        mf_snapshot: MFSnapshot | None,
    ) -> tuple[ResearchDigest, Path, list[Path], Path]:
        jobs: list[tuple[str, str, Any]] = [
            ("equity", holding.tradingsymbol, lambda holding=holding: self._research_equity_holding(holding))
            for holding in portfolio_snapshot.holdings
            if holding.tradingsymbol not in {"LIQUIDBEES", "NIFTYBEES", "GOLDCASE", "SILVERCASE"}
        ]
        jobs.extend(
            ("mf", holding.tradingsymbol or holding.fund, lambda holding=holding: self._research_mf_holding(holding))
            for holding in (mf_snapshot.holdings if mf_snapshot else [])
        )
        results = await self._run_research_jobs(jobs)

        equity_reports: list[EquityResearchArtifact] = []
        mf_reports: list[MFResearchArtifact] = []
        errors: list[str] = []
        per_holding_payloads: dict[str, dict[str, Any]] = {}

        for kind, result in results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue
            key = self._unique_payload_key(kind, result.identifier, per_holding_payloads)
            per_holding_payloads[key] = result.model_dump(mode="json")
            if kind == "equity":
                equity_reports.append(result)
            else:
                mf_reports.append(result)

        digest_text = await self._build_digest_text(equity_reports, mf_reports, errors)
        digest = ResearchDigest(
            generated_at=datetime.now(UTC),
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
            f"Use tavily_search up to {self.settings.analyst_max_searches} times across Screener, latest results, "
            "concalls, investor presentations, "
            "exchange filings, and recent sector/company news. "
            "Return exactly one JSON object wrapped in <equity_research>...</equity_research> tags with keys: "
            "identifier, title, data_freshness, sources, bull_case, bear_case, what_to_watch, red_flags, confidence_summary."
        )
        raw_text = await self._run_tool_loop(
            system=system,
            user_prompt=user_prompt,
            label=f"research_equity:{holding.tradingsymbol}",
            metadata={"phase": "research_equity", "ticker": holding.tradingsymbol},
        )
        payload = self._extract_tagged_json(raw_text, "equity_research", holding.tradingsymbol)
        report = EquityResearchArtifact(
            generated_at=datetime.now(UTC),
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
            f"Use tavily_search up to {self.settings.analyst_max_searches} times across AMC pages, Value Research, "
            "Morningstar if available, factsheets, "
            "portfolio disclosures, and recent fund commentary. "
            "Return exactly one JSON object wrapped in <mf_research>...</mf_research> tags with keys: "
            "identifier, title, data_freshness, sources, fund_house, category, mandate, portfolio_style, "
            "expense_ratio_note, aum_note, overlap_risk, recent_commentary, risks, confidence_summary."
        )
        raw_text = await self._run_tool_loop(
            system=system,
            user_prompt=user_prompt,
            label=f"research_mf:{holding.tradingsymbol or holding.fund}",
            metadata={
                "phase": "research_mf",
                "fund": holding.fund,
                "tradingsymbol": holding.tradingsymbol,
            },
        )
        payload = self._extract_tagged_json(raw_text, "mf_research", holding.fund)
        report = MFResearchArtifact(
            generated_at=datetime.now(UTC),
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

    async def _run_tool_loop(
        self,
        *,
        system: str,
        user_prompt: str,
        label: str,
        metadata: dict[str, Any],
    ) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        searches_used = 0
        for iteration in range(1, self.settings.max_iterations + 1):
            log_estimated_input_tokens(label=f"[{label}]", messages=messages, system=system)
            response = await self.client.messages.create(
                model=self.settings.analyst_model,
                max_tokens=self.settings.analyst_max_tokens,
                system=system,
                messages=messages,
                tools=[self.search_tool],
            )
            record_anthropic_usage(
                settings=self.settings,
                label=label,
                model=self.settings.analyst_model,
                response=response,
                metadata={**metadata, "iteration": iteration},
            )
            stop_reason = getattr(response, "stop_reason", None)
            if stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results: list[dict[str, Any]] = []
                for block in response.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    if getattr(block, "name", "") != "tavily_search":
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(
                                    {"error": f"Unsupported tool requested: {block.name}"}, ensure_ascii=True
                                ),
                                "is_error": True,
                            }
                        )
                        continue
                    if searches_used >= self.settings.analyst_max_searches:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(
                                    {
                                        "error": (
                                            f"tavily_search budget exhausted; max "
                                            f"{self.settings.analyst_max_searches} searches allowed."
                                        )
                                    },
                                    ensure_ascii=True,
                                ),
                                "is_error": True,
                            }
                        )
                        continue
                    try:
                        result = await asyncio.to_thread(
                            tavily_search,
                            query=str(block.input["query"]),
                            max_results=int(block.input.get("max_results", DEFAULT_TAVILY_MAX_RESULTS)),
                            settings=self.settings,
                        )
                        searches_used += 1
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )
                    except Exception as exc:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps({"error": str(exc)}, ensure_ascii=True),
                                "is_error": True,
                            }
                        )
                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                continue
            if stop_reason in {"end_turn", "max_tokens"}:
                return self._extract_text(response)
            raise ResearchExecutionError(f"Unexpected stop_reason in {label}: {stop_reason}")
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
        messages = [
            {
                "role": "user",
                "content": (
                    "Write a concise portfolio research digest for an Indian investor using the supplied "
                    "holding reports. Cover major strengths, concentration risks, and what deserves follow-up. "
                    f"Input JSON:\n{json.dumps(prompt, ensure_ascii=True)}"
                ),
            }
        ]
        log_estimated_input_tokens(label="[research_digest]", messages=messages)
        response = await self.client.messages.create(
            model=self.settings.model,
            max_tokens=self.settings.summary_max_tokens,
            messages=messages,
        )
        record_anthropic_usage(
            settings=self.settings,
            label="research_digest",
            model=self.settings.model,
            response=response,
            metadata={
                "phase": "research_digest",
                "equity_report_count": len(equity_reports),
                "mf_report_count": len(mf_reports),
                "error_count": len(errors),
            },
        )
        return self._extract_text(response) or "No research digest generated."

    async def _run_research_jobs(
        self,
        jobs: list[tuple[str, str, Any]],
    ) -> list[tuple[str, EquityResearchArtifact | MFResearchArtifact | Exception]]:
        if not jobs:
            return []

        semaphore = asyncio.Semaphore(max(self.settings.analyst_parallelism, 1))
        start_lock = asyncio.Lock()
        next_start_at = 0.0
        stagger_seconds = max(self.settings.analyst_min_start_interval_seconds, 0.0)

        async def run_job(
            kind: str,
            _identifier: str,
            factory: Any,
        ) -> tuple[str, EquityResearchArtifact | MFResearchArtifact | Exception]:
            nonlocal next_start_at
            async with semaphore:
                async with start_lock:
                    if stagger_seconds:
                        loop = asyncio.get_running_loop()
                        now = loop.time()
                        wait_seconds = max(next_start_at - now, 0.0)
                        if wait_seconds:
                            await asyncio.sleep(wait_seconds)
                            now = loop.time()
                        next_start_at = now + stagger_seconds
                try:
                    return kind, await factory()
                except Exception as exc:  # pragma: no cover
                    return kind, exc

        tasks = [asyncio.create_task(run_job(kind, identifier, factory)) for kind, identifier, factory in jobs]
        return await asyncio.gather(*tasks)

    @staticmethod
    def _unique_payload_key(kind: str, identifier: str, existing_payloads: dict[str, dict[str, Any]]) -> str:
        base_key = f"{kind}_{identifier}".replace("/", "_").replace(" ", "_").upper()
        candidate = base_key
        suffix = 2
        while candidate in existing_payloads:
            candidate = f"{base_key}_{suffix}"
            suffix += 1
        return candidate

    def _extract_text(self, response: Any) -> str:
        text_parts: list[str] = []
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        return "\n".join(text_parts).strip()

    def _extract_tagged_json(self, raw_text: str, tag: str, identifier: str) -> dict[str, Any]:
        pattern = rf"<{tag}>\s*(\{{.*?\}})\s*</{tag}>"
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
