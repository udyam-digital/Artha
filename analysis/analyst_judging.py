from __future__ import annotations

import json
from typing import Any

from analysis.artifact_builder import (
    _compute_fair_value,
    _fix_internal_consistency,
    _overwrite_report_card_with_data_card,
)
from analysis.source_map import (
    _backfill_source_map_from_urls,
    _inject_data_card_sources,
    _sync_source_map_to_data_sources,
)
from models import AnalystReportCard, Holding


def apply_data_card_overrides(
    *,
    report_card: AnalystReportCard,
    data_card_sections: dict[str, object],
    holding: Holding,
    collected_urls: list[str],
) -> tuple[AnalystReportCard, dict[str, str]]:
    report_card = _fix_internal_consistency(report_card)
    report_card, overwritten_fields = _overwrite_report_card_with_data_card(report_card, data_card_sections)
    computed_fv = _compute_fair_value(data_card_sections)
    if computed_fv:
        report_card.valuation.fair_value_range = computed_fv
        overwritten_fields["valuation.fair_value_range"] = str(computed_fv)
        if report_card.source_map.get("fair_value") in ("Not available", "", None):
            report_card.source_map["fair_value"] = "yfinance API + NSE India API"
        price = report_card.stock_snapshot.current_price
        if price > 0:
            midpoint = (computed_fv[0] + computed_fv[1]) / 2
            mos_pct = (midpoint - price) / price * 100
            report_card.valuation.margin_of_safety = (
                f"+{mos_pct:.1f}% (discount)" if mos_pct >= 0 else f"{mos_pct:.1f}% (premium)"
            )
            overwritten_fields["valuation.margin_of_safety"] = report_card.valuation.margin_of_safety
    report_card = _inject_data_card_sources(report_card, data_card_sections)
    if collected_urls:
        ticker_lower = holding.tradingsymbol.lower()
        relevant_urls = [
            url
            for url in collected_urls
            if ticker_lower in url.lower()
            or any(
                keyword in url.lower()
                for keyword in [
                    "screener.in",
                    "moneycontrol.com",
                    "trendlyne.com",
                    "tickertape.in",
                    "bseindia.com",
                    "nseindia.com",
                    "stockanalysis.com",
                ]
            )
        ]
        urls_to_add = relevant_urls if relevant_urls else list(dict.fromkeys(collected_urls))[:5]
        existing = set(report_card.data_sources)
        for url in list(dict.fromkeys(urls_to_add)):
            if url not in existing:
                report_card.data_sources.append(url)
                existing.add(url)
        report_card = _backfill_source_map_from_urls(report_card, urls_to_add)
    return _sync_source_map_to_data_sources(report_card), overwritten_fields


def build_judge_context(data_card_sections: dict[str, object], overwritten_fields: dict[str, str]) -> tuple[str, str]:
    data_card_summary = {
        "price": {
            "cmp": data_card_sections.get("price_data", {}).get("cmp"),
            "vs_200dma_pct": data_card_sections.get("price_data", {}).get("vs_200dma_pct"),
            "alpha_vs_nifty": data_card_sections.get("price_data", {}).get("alpha_vs_nifty_52w_pct"),
        },
        "valuation": {
            "trailing_pe": data_card_sections.get("valuation", {}).get("trailing_pe"),
            "sector_pe": data_card_sections.get("valuation", {}).get("sector_pe"),
            "pe_premium_pct": data_card_sections.get("valuation", {}).get("pe_premium_to_sector_pct"),
            "peg": data_card_sections.get("valuation", {}).get("peg_ratio"),
            "analyst_target_mean": data_card_sections.get("valuation", {}).get("analyst_target_mean"),
        },
        "financials": {
            "debt_to_equity": data_card_sections.get("financials", {}).get("debt_to_equity"),
            "ebitda_margin_pct": data_card_sections.get("financials", {}).get("ebitda_margin_pct"),
            "net_cash": data_card_sections.get("financials", {}).get("net_cash"),
            "revenue_growth_pct": data_card_sections.get("financials", {}).get("revenue_growth_pct"),
        },
    }
    return json.dumps(data_card_summary, ensure_ascii=True), json.dumps(overwritten_fields, indent=2, ensure_ascii=True)


def combined_overall(quality_scores: dict[str, Any] | None, factual_scores: dict[str, Any] | None) -> float:
    quality = float(quality_scores.get("overall", 0)) if quality_scores else 0.0
    factual = float(factual_scores.get("overall", 0)) if factual_scores else 0.0
    if quality_scores and factual_scores:
        return quality * 0.5 + factual * 0.5
    return quality or factual


def build_retry_context(
    *,
    combined_score: float,
    quality_scores: dict[str, Any] | None,
    factual_scores: dict[str, Any] | None,
    collected_urls: list[str],
) -> str:
    issues = list((quality_scores or {}).get("key_issues", []))
    issues.extend((factual_scores or {}).get("red_flags", []))
    parts = [f"RETRY — Your previous analysis scored {combined_score:.0f}/100. Fix these issues:"]
    parts.extend(f"- {issue}" for issue in issues[:5])
    if collected_urls:
        parts.append("")
        parts.append("Source URLs available from your searches (USE THESE in source_map and data_sources):")
        parts.extend(f"- {url}" for url in list(dict.fromkeys(collected_urls)))
    return "\n".join(parts)
