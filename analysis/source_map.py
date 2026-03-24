from __future__ import annotations

import json
import re

from models import AnalystReportCard

_SOURCE_MAP_KEY_ALIASES: dict[str, str] = {
    # revenue_cagr aliases
    "revenue": "revenue_cagr",
    "revenue_growth": "revenue_cagr",
    "revenue_yoy": "revenue_cagr",
    "q3_fy26_revenue": "revenue_cagr",
    "q2_fy26_revenue": "revenue_cagr",
    "q4_fy26_revenue": "revenue_cagr",
    "q3_revenue": "revenue_cagr",
    "revenue_cagr_source": "revenue_cagr",
    # eps_cagr aliases
    "eps": "eps_cagr",
    "eps_growth": "eps_cagr",
    "net_profit": "eps_cagr",
    "q3_fy26_netprofit": "eps_cagr",
    "q3_diluted_eps": "eps_cagr",
    "eps_cagr_source": "eps_cagr",
    "q3_fy26_eps": "eps_cagr",
    "netprofit": "eps_cagr",
    # roce aliases
    "roce_source": "roce",
    "return_on_capital": "roce",
    # roe aliases
    "roe_source": "roe",
    "return_on_equity": "roe",
    # pe aliases
    "pe_ratio": "pe",
    "pe_source": "pe",
    "trailing_pe": "pe",
    # peg aliases
    "peg_ratio": "peg",
    "peg_source": "peg",
    # fcf_yield aliases
    "fcf": "fcf_yield",
    "free_cash_flow": "fcf_yield",
    "fcf_yield_source": "fcf_yield",
    # debt_to_equity aliases
    "de_ratio": "debt_to_equity",
    "debt_equity": "debt_to_equity",
    "d_e_ratio": "debt_to_equity",
    "debt_to_equity_source": "debt_to_equity",
    # fair_value aliases
    "fair_value_range": "fair_value",
    "fair_value_source": "fair_value",
    "valuation": "fair_value",
    # risk_1 aliases
    "risk": "risk_1",
    "primary_risk": "risk_1",
    "risk_1_source": "risk_1",
    # analyst_target aliases
    "target_price": "analyst_target",
    "consensus_target": "analyst_target",
    # market_share aliases
    "market_position": "market_share",
    "competitive_position": "market_share",
}

REQUIRED_SOURCE_MAP_KEYS = [
    "revenue_cagr",
    "eps_cagr",
    "roce",
    "roe",
    "pe",
    "peg",
    "fcf_yield",
    "debt_to_equity",
    "fair_value",
    "risk_1",
    "analyst_target",
    "market_share",
]

DATA_CARD_SOURCE_MAP: dict[str, tuple[str, str, str]] = {
    "pe": ("valuation", "trailing_pe", "yfinance API"),
    "roe": ("quality", "roe_proxy_pct", "yfinance API"),
    "roce": ("quality", "roce_proxy_pct", "yfinance API"),
    "debt_to_equity": ("financials", "debt_to_equity", "yfinance API"),
    "peg": ("valuation", "peg_ratio", "yfinance API"),
    "revenue_cagr": ("nse_quarterly", "revenue_yoy_pct", "NSE India API"),
    "eps_cagr": ("nse_quarterly", "eps_qoq_pct", "NSE India API"),
    "fcf_yield": ("financials", "ebitda_margin_pct", "yfinance API"),
    "analyst_target": ("valuation", "analyst_target_mean", "yfinance API"),
    "market_share": ("meta", "index_memberships", "NSE India API"),
}


def _is_valid_source_map_value(value: str) -> bool:
    """Check if a source_map value is a URL, 'Not available', or a known API provider."""
    v = value.strip()
    return v.startswith("http") or v.lower() in ("not available", "yfinance api", "nse india api")


def _normalize_source_map_keys(source_map: dict[str, str]) -> dict[str, str]:
    """Normalize LLM-generated source_map keys to the 12 required standard keys.
    Also filters out data values (non-URLs) that the LLM sometimes puts in source_map."""
    normalized: dict[str, str] = {}
    # First pass: copy entries with standard keys (only if value is URL or "Not available")
    for key, value in source_map.items():
        if not _is_valid_source_map_value(value):
            continue  # Skip data values like "₹319 cr, +20% YoY"
        lower_key = key.lower().strip()
        if lower_key in REQUIRED_SOURCE_MAP_KEYS:
            if lower_key not in normalized or normalized[lower_key] == "Not available":
                normalized[lower_key] = value
        else:
            # Try alias mapping
            mapped = _SOURCE_MAP_KEY_ALIASES.get(lower_key)
            if mapped and (mapped not in normalized or normalized[mapped] == "Not available"):
                normalized[mapped] = value
    # Ensure all 12 required keys exist
    for key in REQUIRED_SOURCE_MAP_KEYS:
        if key not in normalized:
            normalized[key] = "Not available"
    return normalized


def _extract_source_map_from_raw(raw_text: str) -> dict[str, str]:
    """Extract source_map from raw LLM JSON text before instructor coercion can drop it."""
    try:
        parsed = json.loads(raw_text)
        sm = parsed.get("source_map", {})
        if isinstance(sm, dict):
            return {str(k): str(v) for k, v in sm.items()}
    except (json.JSONDecodeError, Exception):
        pass
    # Fallback: try to find source_map in partial JSON
    match = re.search(r'"source_map"\s*:\s*\{([^}]+)\}', raw_text, re.DOTALL)
    if match:
        try:
            sm = json.loads("{" + match.group(1) + "}")
            return {str(k): str(v) for k, v in sm.items()}
        except (json.JSONDecodeError, Exception):
            pass
    return {}


def _extract_data_sources_from_raw(raw_text: str) -> list[str]:
    """Extract data_sources from raw LLM JSON text as backup."""
    try:
        parsed = json.loads(raw_text)
        ds = parsed.get("data_sources", [])
        if isinstance(ds, list):
            return [str(u) for u in ds if str(u).startswith("http")]
    except (json.JSONDecodeError, Exception):
        pass
    return []


def _backfill_source_map_from_urls(
    report_card: AnalystReportCard,
    collected_urls: list[str],
) -> AnalystReportCard:
    """Try to fill 'Not available' source_map entries using heuristics on collected URLs."""
    url_pool = list(dict.fromkeys(list(report_card.data_sources) + collected_urls))
    url_metric_hints: list[tuple[list[str], list[str]]] = [
        (
            ["screener.in", "stockanalysis.com", "moneycontrol.com/financials"],
            ["roce", "roe", "pe", "debt_to_equity", "fcf_yield", "peg"],
        ),
        (["trendlyne.com", "tickertape.in"], ["pe", "peg", "fair_value"]),
        (["livemint.com", "bseindia.com", "nseindia.com"], ["revenue_cagr", "eps_cagr"]),
        (["results", "earnings", "quarterly", "q3", "q2", "q4"], ["revenue_cagr", "eps_cagr"]),
        (["analyst", "target", "consensus", "rating"], ["analyst_target"]),
        (["risk", "outlook", "competitor"], ["risk_1"]),
    ]
    for url in url_pool:
        url_lower = url.lower()
        for patterns, metric_keys in url_metric_hints:
            if any(p in url_lower for p in patterns):
                for mk in metric_keys:
                    if report_card.source_map.get(mk) == "Not available":
                        report_card.source_map[mk] = url
                        break  # Only fill one metric per URL pattern match
    return report_card


def _sync_source_map_to_data_sources(report_card: AnalystReportCard) -> AnalystReportCard:
    """Ensure every URL in source_map also appears in data_sources."""
    existing = set(report_card.data_sources)
    added: list[str] = []
    for url in report_card.source_map.values():
        if url and url.startswith("http") and url not in existing:
            added.append(url)
            existing.add(url)
    if added:
        report_card.data_sources = list(report_card.data_sources) + added
    return report_card


def _inject_data_card_sources(
    report_card: AnalystReportCard,
    data_card_sections: dict,
) -> AnalystReportCard:
    """Fill 'Not available' source_map entries with API provider name when data card has a value."""
    for metric_key, (section, field, provider) in DATA_CARD_SOURCE_MAP.items():
        current_value = report_card.source_map.get(metric_key, "Not available")
        if current_value.strip().lower() not in ("not available", ""):
            continue  # already has a real source
        section_data = data_card_sections.get(section)
        if not isinstance(section_data, dict):
            continue
        field_value = section_data.get(field)
        # For list fields (like index_memberships), check non-empty list
        if isinstance(field_value, list):
            has_value = len(field_value) > 0
        else:
            has_value = field_value is not None
        if has_value:
            report_card.source_map[metric_key] = provider
    return report_card
