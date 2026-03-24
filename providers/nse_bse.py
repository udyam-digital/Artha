"""nse-bse-mcp provider — corporate announcements, bulk deals, and earnings calendar.

Requires a running nse-bse-mcp HTTP server. Set NSE_BSE_MCP_URL in .env to enable.
Start the server with: npx nse-bse-mcp (then set NSE_BSE_MCP_URL=http://localhost:PORT)
All functions return empty results gracefully when the URL is not configured.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from config import Settings, get_settings
from providers.mcp_client import MCPServerDefinition, MCPToolClient

logger = logging.getLogger(__name__)

_ANNOUNCEMENT_HEADLINE_MAX = 200
_MAX_ANNOUNCEMENTS = 15
_MAX_BULK_DEALS = 20

# Keywords for flag detection (lowercased)
_GUIDANCE_KEYWORDS = ("guidance", "outlook", "revised", "upgrade", "downgrade", "profit warning", "revenue guidance")
_MANAGEMENT_KEYWORDS = (
    "resign",
    "appoint",
    "director",
    "chief executive",
    "ceo",
    "cfo",
    "managing director",
    " md ",
    "change in management",
    "key managerial",
)
_PLEDGE_KEYWORDS = ("pledg", "encumber", "creation of charge", "revocation of pledge")
_AUDIT_KEYWORDS = ("auditor", "audit qualif", "emphasis of matter", "resignation of statutory", "change in auditor")


def _nse_bse_definition(settings: Settings) -> MCPServerDefinition:
    return MCPServerDefinition(
        transport="http",
        url=settings.nse_bse_mcp_url.rstrip("/"),
        command="",
        args=[],
        env={},
    )


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _extract_announcement_text(ann: dict) -> str:
    for key in ("ann_desc", "desc", "subject", "headline", "announcement", "text"):
        val = ann.get(key)
        if val and isinstance(val, str):
            return val.strip()[:_ANNOUNCEMENT_HEADLINE_MAX]
    return ""


def _extract_announcement_date(ann: dict) -> str:
    for key in ("curr_filing_date", "filing_date", "date", "sort_date", "bm_date"):
        val = ann.get(key)
        if val and isinstance(val, str):
            return val.strip()[:10]
    return ""


def _detect_flags(filings: list[dict]) -> dict[str, bool]:
    """Scan all filing headlines for story-breaker flags."""
    has_guidance = has_management = has_pledge = has_audit = False
    for f in filings:
        text = (f.get("headline") or "").lower()
        if not has_guidance and any(kw in text for kw in _GUIDANCE_KEYWORDS):
            has_guidance = True
        if not has_management and any(kw in text for kw in _MANAGEMENT_KEYWORDS):
            has_management = True
        if not has_pledge and any(kw in text for kw in _PLEDGE_KEYWORDS):
            has_pledge = True
        if not has_audit and any(kw in text for kw in _AUDIT_KEYWORDS):
            has_audit = True
    return {
        "has_guidance_update": has_guidance,
        "has_management_change": has_management,
        "has_pledging_update": has_pledge,
        "has_audit_issue": has_audit,
    }


async def get_corporate_announcements(
    ticker: str,
    days: int = 30,
    settings: Settings | None = None,
) -> list[dict]:
    """Fetch recent corporate announcements for ticker via nse-bse-mcp.

    Returns list of dicts with keys: date, headline, category, url.
    Returns [] if NSE_BSE_MCP_URL is not configured or fetch fails.
    """
    settings = settings or get_settings()
    if not settings.nse_bse_mcp_url.strip():
        return []

    symbol = str(ticker).strip().upper()
    to_date = datetime.now(UTC)
    from_date = to_date - timedelta(days=days)

    try:
        definition = _nse_bse_definition(settings)
        async with MCPToolClient(definition, timeout_seconds=settings.nse_bse_mcp_timeout_seconds) as client:
            raw = await client.call_tool(
                "nse_corporate_announcements",
                {
                    "symbol": symbol,
                    "from_date": _fmt_date(from_date),
                    "to_date": _fmt_date(to_date),
                },
            )
    except Exception as exc:
        logger.warning("[nse_bse] announcements fetch failed for %s: %s", symbol, exc)
        return []

    items: list[Any] = (
        raw
        if isinstance(raw, list)
        else (raw.get("data") or raw.get("announcements") or [] if isinstance(raw, dict) else [])
    )

    result: list[dict] = []
    for ann in items[:_MAX_ANNOUNCEMENTS]:
        if not isinstance(ann, dict):
            continue
        headline = _extract_announcement_text(ann)
        if not headline:
            continue
        result.append(
            {
                "date": _extract_announcement_date(ann),
                "headline": headline,
                "category": str(ann.get("sub_type") or ann.get("category") or ann.get("type") or "").strip(),
                "url": str(ann.get("csvName") or ann.get("url") or ann.get("link") or "").strip(),
            }
        )

    return result


async def get_bulk_deals(
    ticker: str,
    days: int = 30,
    settings: Settings | None = None,
) -> list[dict]:
    """Fetch recent bulk deals for ticker via nse-bse-mcp (filters response by symbol).

    Returns list of dicts with keys: date, client, direction, quantity, price.
    Returns [] if NSE_BSE_MCP_URL is not configured or fetch fails.
    """
    settings = settings or get_settings()
    if not settings.nse_bse_mcp_url.strip():
        return []

    symbol = str(ticker).strip().upper()
    to_date = datetime.now(UTC)
    from_date = to_date - timedelta(days=days)

    try:
        definition = _nse_bse_definition(settings)
        async with MCPToolClient(definition, timeout_seconds=settings.nse_bse_mcp_timeout_seconds) as client:
            raw = await client.call_tool(
                "nse_bulk_deals",
                {
                    "from_date": _fmt_date(from_date),
                    "to_date": _fmt_date(to_date),
                },
            )
    except Exception as exc:
        logger.warning("[nse_bse] bulk deals fetch failed for %s: %s", symbol, exc)
        return []

    items: list[Any] = raw if isinstance(raw, list) else (raw.get("data") or [] if isinstance(raw, dict) else [])

    result: list[dict] = []
    for deal in items:
        if not isinstance(deal, dict):
            continue
        deal_symbol = str(deal.get("symbol") or deal.get("Symbol") or "").strip().upper()
        if deal_symbol != symbol:
            continue
        direction_raw = (
            str(deal.get("buyOrSell") or deal.get("buy_sell") or deal.get("direction") or "").strip().upper()
        )
        direction = (
            "BUY" if direction_raw.startswith("B") else "SELL" if direction_raw.startswith("S") else direction_raw
        )
        qty_raw = deal.get("quantity") or deal.get("Quantity") or deal.get("qty") or 0
        price_raw = deal.get("rate") or deal.get("price") or deal.get("Rate") or deal.get("Price") or 0
        date_raw = str(deal.get("tradeDate") or deal.get("date") or deal.get("Date") or "").strip()[:10]
        client_name = str(deal.get("clientName") or deal.get("client") or deal.get("Client") or "").strip()
        try:
            qty = float(qty_raw)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            price = 0.0
        result.append(
            {
                "date": date_raw,
                "client": client_name[:80],
                "direction": direction,
                "quantity": qty,
                "price": price,
            }
        )
        if len(result) >= _MAX_BULK_DEALS:
            break

    return result


async def get_earnings_calendar(
    days_ahead: int = 21,
    settings: Settings | None = None,
) -> list[dict]:
    """Fetch upcoming earnings result dates via nse-bse-mcp (BSE result calendar).

    Returns list of dicts with keys: company, result_date, result_type.
    Returns [] if NSE_BSE_MCP_URL is not configured or fetch fails.
    """
    settings = settings or get_settings()
    if not settings.nse_bse_mcp_url.strip():
        return []

    from_date = datetime.now(UTC)
    to_date = from_date + timedelta(days=days_ahead)

    try:
        definition = _nse_bse_definition(settings)
        async with MCPToolClient(definition, timeout_seconds=settings.nse_bse_mcp_timeout_seconds) as client:
            raw = await client.call_tool(
                "bse_result_calendar",
                {
                    "from_date": _fmt_date(from_date),
                    "to_date": _fmt_date(to_date),
                },
            )
    except Exception as exc:
        logger.warning("[nse_bse] earnings calendar fetch failed: %s", exc)
        return []

    items: list[Any] = raw if isinstance(raw, list) else (raw.get("data") or [] if isinstance(raw, dict) else [])

    result: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        company = str(item.get("companyname") or item.get("company") or item.get("name") or "").strip()
        result_date = str(item.get("resultdate") or item.get("result_date") or item.get("date") or "").strip()[:10]
        result_type = str(item.get("resulttype") or item.get("result_type") or item.get("type") or "").strip()
        if company and result_date:
            result.append({"company": company, "result_date": result_date, "result_type": result_type})

    return result
