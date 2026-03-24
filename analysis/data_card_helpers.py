from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _round2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return _round2((new - old) / abs(old) * 100)


def _parse_shareholding_history(sh_data: Any) -> list[dict[str, Any]]:
    if not isinstance(sh_data, dict):
        return []
    history: list[dict[str, Any]] = []
    for date_str, entries in sh_data.items():
        if not isinstance(entries, list):
            continue
        record: dict[str, Any] = {"date": date_str}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key, value in entry.items():
                try:
                    record[key] = float(str(value).strip())
                except (TypeError, ValueError):
                    record[key] = value
        history.append(record)
    try:
        history.sort(key=lambda row: datetime.strptime(str(row["date"]), "%d-%b-%Y"))
    except Exception:
        pass
    return history


def _extract_financial_inputs(yf_raw: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    return (
        _safe_float(yf_raw.get("trailingEps") or yf_raw.get("epsTrailingTwelveMonths")),
        _safe_float(yf_raw.get("bookValue")),
        _safe_float(yf_raw.get("totalCash")),
        _safe_float(yf_raw.get("totalDebt")),
    )


def build_dividends_section(*, yf_raw: dict[str, Any], nse_corp_actions: list[Any]) -> dict[str, Any]:
    def epoch_to_date(epoch: Any) -> str | None:
        value = _safe_float(epoch)
        if value is None:
            return None
        try:
            return datetime.utcfromtimestamp(value).strftime("%Y-%m-%d")
        except Exception:
            return None

    return {
        "dividend_yield_pct": _safe_float(yf_raw.get("dividendYield")),
        "payout_ratio": _safe_float(yf_raw.get("payoutRatio")),
        "five_yr_avg_dividend_yield": _safe_float(yf_raw.get("fiveYearAvgDividendYield")),
        "last_dividend_amount": _safe_float(yf_raw.get("lastDividendValue")),
        "last_dividend_date": epoch_to_date(yf_raw.get("lastDividendDate")),
        "next_earnings_date": epoch_to_date(yf_raw.get("earningsTimestampStart")),
        "recent_corporate_actions": [dict(entry) for entry in nse_corp_actions[:5] if isinstance(entry, dict)]
        if isinstance(nse_corp_actions, list)
        else [],
    }


def build_recent_filings_section(announcements_raw: list[dict], errors: list[str] | None = None) -> dict:
    filings = (
        [filing for filing in announcements_raw if isinstance(filing, dict)]
        if isinstance(announcements_raw, list)
        else []
    )
    keywords = {
        "has_guidance_update": (
            "guidance",
            "outlook",
            "revised",
            "upgrade",
            "downgrade",
            "profit warning",
            "revenue guidance",
        ),
        "has_management_change": (
            "resign",
            "appoint",
            "director",
            "chief executive",
            "ceo",
            "cfo",
            "managing director",
            " md ",
            "key managerial",
        ),
        "has_pledging_update": ("pledg", "encumber", "creation of charge", "revocation of pledge"),
        "has_audit_issue": (
            "auditor",
            "audit qualif",
            "emphasis of matter",
            "resignation of statutory",
            "change in auditor",
        ),
    }
    flags = {name: False for name in keywords}
    for filing in filings:
        headline = (filing.get("headline") or "").lower()
        for field, values in keywords.items():
            flags[field] = flags[field] or any(keyword in headline for keyword in values)
    return {"filings": filings, **flags, "fetch_errors": list(errors or [])}


def build_bulk_deals_section(ticker: str, deals_raw: list[dict], errors: list[str] | None = None) -> dict:
    del ticker
    deals = [deal for deal in deals_raw if isinstance(deal, dict)] if isinstance(deals_raw, list) else []
    total_buy = sum(
        float(deal.get("quantity") or 0) for deal in deals if str(deal.get("direction") or "").upper() == "BUY"
    )
    total_sell = sum(
        float(deal.get("quantity") or 0) for deal in deals if str(deal.get("direction") or "").upper() == "SELL"
    )
    if total_buy > 0 and total_sell > 0:
        ratio = min(total_buy, total_sell) / max(total_buy, total_sell)
        net_direction = "Mixed" if ratio > 0.5 else ("Buying" if total_buy > total_sell else "Selling")
    elif total_buy > 0:
        net_direction = "Buying"
    elif total_sell > 0:
        net_direction = "Selling"
    else:
        net_direction = "None"
    return {
        "deals": deals,
        "net_direction": net_direction,
        "total_buy_qty": round(total_buy, 0),
        "total_sell_qty": round(total_sell, 0),
        "fetch_errors": list(errors or []),
    }
