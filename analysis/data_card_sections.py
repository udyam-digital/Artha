from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from analysis.data_card_helpers import (
    _extract_financial_inputs,
    _parse_shareholding_history,
    _pct_change,
    _round2,
    _safe_float,
    _safe_int,
    build_dividends_section,
)


def build_company_data_card(
    *,
    ticker: str,
    exchange: str,
    yf_raw: dict,
    nse_raw: dict,
    price_context: dict,
) -> dict:
    now = datetime.now(UTC)
    details = nse_raw.get("details") or {}
    trade_info = nse_raw.get("trade_info") or {}
    corporate_info = nse_raw.get("corporate_info") or {}
    nse_info = details.get("info") or {}
    nse_metadata = details.get("metadata") or {}
    nse_security_info = details.get("securityInfo") or {}
    nse_price_info = details.get("priceInfo") or {}
    nse_industry_info = details.get("industryInfo") or {}
    nse_pre_open = details.get("preOpenMarket") or {}
    nse_market_book = trade_info.get("marketDeptOrderBook") or {}
    nse_trade_data = nse_market_book.get("tradeInfo") or {}
    nse_var = nse_market_book.get("valueAtRisk") or {}
    nse_dp = trade_info.get("securityWiseDP") or {}
    nse_financial_results = corporate_info.get("financial_results", {}).get("data") or []
    nse_shareholdings = corporate_info.get("shareholdings_patterns", {}).get("data") or {}
    nse_corp_actions = corporate_info.get("corporate_actions", {}).get("data") or []
    cmp = _safe_float(yf_raw.get("currentPrice") or yf_raw.get("regularMarketPrice"))
    trailing_eps, book_value, total_cash, total_debt = _extract_financial_inputs(yf_raw)
    return {
        "meta": _build_meta(
            now=now,
            ticker=ticker,
            exchange=exchange,
            nse_info=nse_info,
            nse_metadata=nse_metadata,
            nse_security_info=nse_security_info,
            nse_industry_info=nse_industry_info,
        ),
        "price_data": _build_price_data(
            yf_raw=yf_raw,
            nse_price_info=nse_price_info,
            nse_trade_data=nse_trade_data,
            cmp=cmp,
            price_context=price_context,
        ),
        "valuation": _build_valuation(yf_raw=yf_raw, nse_metadata=nse_metadata, cmp=cmp, trailing_eps=trailing_eps),
        "financials": _build_financials(
            yf_raw=yf_raw,
            nse_trade_data=nse_trade_data,
            trailing_eps=trailing_eps,
            book_value=book_value,
            total_cash=total_cash,
            total_debt=total_debt,
        ),
        "nse_quarterly": _build_nse_quarterly(nse_financial_results),
        "quality": _build_quality(
            yf_raw=yf_raw,
            nse_trade_data=nse_trade_data,
            nse_var=nse_var,
            nse_dp=nse_dp,
            book_value=book_value,
            total_debt=total_debt,
        ),
        "ownership": _build_ownership(yf_raw=yf_raw, nse_shareholdings=nse_shareholdings),
        "dividends_corporate": build_dividends_section(yf_raw=yf_raw, nse_corp_actions=nse_corp_actions),
        "technical_signals": _build_technicals(
            cmp=cmp,
            nse_price_info=nse_price_info,
            nse_pre_open=nse_pre_open,
            nse_dp=nse_dp,
            nse_trade_data=nse_trade_data,
        ),
    }


def _build_meta(
    *,
    now: datetime,
    ticker: str,
    exchange: str,
    nse_info: dict,
    nse_metadata: dict,
    nse_security_info: dict,
    nse_industry_info: dict,
) -> dict:
    surveillance = nse_security_info.get("surveillance") or {}
    surv_value = surveillance.get("surv", "") if isinstance(surveillance, dict) else ""
    index_memberships = nse_metadata.get("pdSectorIndAll")
    if isinstance(index_memberships, list):
        memberships = [str(value) for value in index_memberships if value]
    elif isinstance(index_memberships, str) and index_memberships:
        memberships = [index_memberships]
    else:
        memberships = []
    return {
        "ticker": ticker.upper(),
        "exchange": exchange.upper(),
        "isin": nse_info.get("isin") or nse_metadata.get("isin"),
        "company_name": nse_info.get("companyName") or nse_info.get("symbol") or ticker.upper(),
        "listing_date": nse_info.get("listingDate") or nse_metadata.get("listingDate"),
        "face_value": _safe_float(nse_security_info.get("faceValue")),
        "sector": nse_industry_info.get("sector") or nse_industry_info.get("macro"),
        "industry": nse_industry_info.get("industry"),
        "industry_macro": nse_industry_info.get("macro"),
        "industry_4l": nse_industry_info.get("basicIndustry"),
        "index_memberships": memberships,
        "is_fno": bool(nse_info.get("isFNOSec", False)),
        "is_slb": bool(nse_info.get("isSLBSec", False)),
        "is_under_surveillance": bool(surv_value and str(surv_value).strip() not in ("", "-", "None")),
        "surveillance_stage": str(surveillance.get("desc", "")).strip() or None if surv_value else None,
        "fetched_at": now,
    }


def _build_price_data(
    *, yf_raw: dict, nse_price_info: dict, nse_trade_data: dict, cmp: float | None, price_context: dict
) -> dict:
    vwap = _safe_float(nse_price_info.get("vwap"))
    week_hl = nse_price_info.get("weekHighLow") or {}
    dma_50 = _safe_float(yf_raw.get("fiftyDayAverage"))
    dma_200 = _safe_float(yf_raw.get("twoHundredDayAverage"))
    return {
        "cmp": cmp,
        "prev_close": _safe_float(nse_price_info.get("previousClose") or yf_raw.get("previousClose")),
        "day_high": _safe_float(
            (nse_price_info.get("intraDayHighLow") or {}).get("max")
            if isinstance(nse_price_info.get("intraDayHighLow"), dict)
            else yf_raw.get("dayHigh")
        ),
        "day_low": _safe_float(
            (nse_price_info.get("intraDayHighLow") or {}).get("min")
            if isinstance(nse_price_info.get("intraDayHighLow"), dict)
            else yf_raw.get("dayLow")
        ),
        "vwap": vwap,
        "change_pct_today": _safe_float(nse_price_info.get("pChange")),
        "week_52_high": _safe_float(week_hl.get("max") if isinstance(week_hl, dict) else None),
        "week_52_low": _safe_float(week_hl.get("min") if isinstance(week_hl, dict) else None),
        "week_52_high_date": str(week_hl.get("maxDate", "")).strip() or None if isinstance(week_hl, dict) else None,
        "week_52_low_date": str(week_hl.get("minDate", "")).strip() or None if isinstance(week_hl, dict) else None,
        "vs_52w_high_pct": _safe_float(price_context.get("current_vs_52w_high_pct")),
        "dma_50": dma_50,
        "dma_200": dma_200,
        "vs_50dma_pct": _round2((cmp / dma_50 - 1) * 100) if cmp is not None and dma_50 not in (None, 0) else None,
        "vs_200dma_pct": _round2((cmp / dma_200 - 1) * 100) if cmp is not None and dma_200 not in (None, 0) else None,
        "alpha_vs_nifty_52w_pct": _round2(
            ((_safe_float(yf_raw.get("52WeekChange")) or 0) - (_safe_float(yf_raw.get("SandP52WeekChange")) or 0)) * 100
        )
        if _safe_float(yf_raw.get("52WeekChange")) is not None
        and _safe_float(yf_raw.get("SandP52WeekChange")) is not None
        else None,
        "beta": _safe_float(yf_raw.get("beta")),
        "annual_volatility_pct": _safe_float(nse_trade_data.get("cmAnnualVolatility")),
    }


def _build_valuation(*, yf_raw: dict, nse_metadata: dict, cmp: float | None, trailing_eps: float | None) -> dict:
    trailing_pe = _safe_float(yf_raw.get("trailingPE"))
    sector_pe = _safe_float(nse_metadata.get("pdSectorPe"))
    analyst_target_mean = _safe_float(yf_raw.get("targetMeanPrice"))
    return {
        "trailing_pe": trailing_pe,
        "forward_pe": _safe_float(yf_raw.get("forwardPE")),
        "sector_pe": sector_pe,
        "pe_premium_to_sector_pct": _round2((trailing_pe / sector_pe - 1) * 100)
        if trailing_pe not in (None, 0) and sector_pe not in (None, 0)
        else None,
        "price_to_book": _safe_float(yf_raw.get("priceToBook")),
        "price_to_sales": _safe_float(yf_raw.get("priceToSalesTrailing12Months")),
        "ev_ebitda": _safe_float(yf_raw.get("enterpriseToEbitda")),
        "ev_revenue": _safe_float(yf_raw.get("enterpriseToRevenue")),
        "peg_ratio": _safe_float(yf_raw.get("trailingPegRatio")),
        "earnings_yield_pct": _round2((trailing_eps / cmp) * 100)
        if trailing_eps is not None and cmp not in (None, 0)
        else None,
        "analyst_target_mean": analyst_target_mean,
        "analyst_target_median": _safe_float(yf_raw.get("targetMedianPrice")),
        "analyst_upside_pct": _round2((analyst_target_mean / cmp - 1) * 100)
        if analyst_target_mean is not None and cmp not in (None, 0)
        else None,
        "analyst_count": _safe_int(yf_raw.get("numberOfAnalystOpinions")),
        "analyst_consensus": str(yf_raw.get("recommendationKey", "") or "").strip() or None,
    }


def _build_financials(
    *,
    yf_raw: dict,
    nse_trade_data: dict,
    trailing_eps: float | None,
    book_value: float | None,
    total_cash: float | None,
    total_debt: float | None,
) -> dict:
    def margin_pct(key: str) -> float | None:
        value = _safe_float(yf_raw.get(key))
        return _round2((value or 0) * 100) if value is not None else None

    market_cap_crore = _safe_float(nse_trade_data.get("totalMarketCap"))
    ffmc_crore = _safe_float(nse_trade_data.get("ffmc"))
    return {
        "market_cap": _round2(market_cap_crore * 1e7)
        if market_cap_crore is not None
        else _safe_float(yf_raw.get("marketCap")),
        "free_float_market_cap": _round2(ffmc_crore * 1e7) if ffmc_crore is not None else None,
        "enterprise_value": _safe_float(yf_raw.get("enterpriseValue")),
        "total_revenue": _safe_float(yf_raw.get("totalRevenue")),
        "revenue_per_share": _safe_float(yf_raw.get("revenuePerShare")),
        "gross_profit": _safe_float(yf_raw.get("grossProfits")),
        "gross_margin_pct": margin_pct("grossMargins"),
        "ebitda": _safe_float(yf_raw.get("ebitda")),
        "ebitda_margin_pct": margin_pct("ebitdaMargins"),
        "operating_margin_pct": margin_pct("operatingMargins"),
        "net_income": _safe_float(yf_raw.get("netIncomeToCommon")),
        "profit_margin_pct": margin_pct("profitMargins"),
        "trailing_eps": trailing_eps,
        "forward_eps": _safe_float(yf_raw.get("forwardEps") or yf_raw.get("epsForward")),
        "total_cash": total_cash,
        "total_debt": total_debt,
        "net_cash": _round2(total_cash - total_debt) if total_cash is not None and total_debt is not None else None,
        "debt_to_equity": _safe_float(yf_raw.get("debtToEquity")),
        "revenue_growth_pct": _round2((_safe_float(yf_raw.get("revenueGrowth")) or 0) * 100)
        if _safe_float(yf_raw.get("revenueGrowth")) is not None
        else None,
        "earnings_growth_pct": _round2((_safe_float(yf_raw.get("earningsGrowth")) or 0) * 100)
        if _safe_float(yf_raw.get("earningsGrowth")) is not None
        else None,
        "book_value_per_share": book_value,
    }


def _build_nse_quarterly(financial_results: list[Any]) -> dict:
    quarters = []
    for entry in financial_results if isinstance(financial_results, list) else []:
        if not isinstance(entry, dict):
            continue
        period = str(entry.get("to_date") or entry.get("from_date") or "").strip()
        if not period:
            continue
        quarters.append(
            {
                "period": period,
                "_sort_key": period,
                "income": _safe_float(entry.get("income")),
                "pat": _safe_float(entry.get("proLossAftTax")),
                "eps": _safe_float(entry.get("reDilEPS")),
                "pbt": _safe_float(entry.get("reProLossBefTax")),
                "audited": str(entry.get("audited") or "").strip() or None,
            }
        )
    try:
        quarters.sort(key=lambda row: datetime.strptime(row["_sort_key"], "%d %b %Y").strftime("%Y%m%d"), reverse=True)
    except Exception:
        quarters.sort(key=lambda row: row["_sort_key"], reverse=True)
    clean = [{key: value for key, value in quarter.items() if key != "_sort_key"} for quarter in quarters]
    return {
        "quarters": clean,
        "revenue_qoq_pct": _pct_change(clean[0]["income"], clean[1]["income"]) if len(clean) >= 2 else None,
        "revenue_yoy_pct": _pct_change(clean[0]["income"], clean[4]["income"]) if len(clean) >= 5 else None,
        "eps_qoq_pct": _pct_change(clean[0]["eps"], clean[1]["eps"]) if len(clean) >= 2 else None,
        "pat_qoq_pct": _pct_change(clean[0]["pat"], clean[1]["pat"]) if len(clean) >= 2 else None,
    }


def _build_quality(
    *,
    yf_raw: dict,
    nse_trade_data: dict,
    nse_var: dict,
    nse_dp: dict,
    book_value: float | None,
    total_debt: float | None,
) -> dict:
    trailing_eps = _safe_float(yf_raw.get("trailingEps") or yf_raw.get("epsTrailingTwelveMonths"))
    net_income = _safe_float(yf_raw.get("netIncomeToCommon"))
    shares_outstanding = _safe_float(yf_raw.get("sharesOutstanding") or yf_raw.get("impliedSharesOutstanding"))
    roe_proxy = (
        _round2((trailing_eps / book_value) * 100) if trailing_eps is not None and book_value not in (None, 0) else None
    )
    roce_proxy = None
    if net_income is not None and book_value is not None and shares_outstanding not in (None, 0):
        capital_employed = (book_value * shares_outstanding) + (total_debt or 0.0)
        if capital_employed:
            roce_proxy = _round2((net_income / capital_employed) * 100)
    gov_values = [
        value
        for value in (
            _safe_int(yf_raw.get("auditRisk")),
            _safe_int(yf_raw.get("boardRisk")),
            _safe_int(yf_raw.get("compensationRisk")),
            _safe_int(yf_raw.get("overallRisk")),
        )
        if value is not None
    ]
    return {
        "roe_proxy_pct": roe_proxy,
        "roce_proxy_pct": roce_proxy,
        "delivery_pct": _safe_float(nse_dp.get("deliveryToTradedQuantity")),
        "impact_cost": _safe_float(nse_trade_data.get("impactCost")),
        "var_applicable_margin_pct": _safe_float(nse_var.get("applicableMargin")),
        "governance_score": _round2(sum(gov_values) / len(gov_values)) if gov_values else None,
        "audit_risk": _safe_int(yf_raw.get("auditRisk")),
        "board_risk": _safe_int(yf_raw.get("boardRisk")),
        "compensation_risk": _safe_int(yf_raw.get("compensationRisk")),
        "overall_risk_score": _safe_int(yf_raw.get("overallRisk")),
    }


def _build_ownership(*, yf_raw: dict, nse_shareholdings: dict) -> dict:
    shares_outstanding = _safe_float(yf_raw.get("sharesOutstanding") or yf_raw.get("impliedSharesOutstanding"))
    float_shares = _safe_float(yf_raw.get("floatShares"))
    shareholding_history = _parse_shareholding_history(nse_shareholdings)
    promoter_pct = (
        _safe_float(shareholding_history[-1].get("Promoter & Promoter Group")) if shareholding_history else None
    )
    previous_promoter = (
        _safe_float(shareholding_history[-2].get("Promoter & Promoter Group"))
        if len(shareholding_history) >= 2
        else None
    )
    return {
        "shares_outstanding": shares_outstanding,
        "float_shares": float_shares,
        "float_ratio_pct": _round2((float_shares / shares_outstanding) * 100)
        if float_shares is not None and shares_outstanding not in (None, 0)
        else None,
        "institutional_holding_pct": _round2((_safe_float(yf_raw.get("heldPercentInstitutions")) or 0) * 100)
        if _safe_float(yf_raw.get("heldPercentInstitutions")) is not None
        else None,
        "insider_holding_pct": _round2((_safe_float(yf_raw.get("heldPercentInsiders")) or 0) * 100)
        if _safe_float(yf_raw.get("heldPercentInsiders")) is not None
        else None,
        "promoter_holding_pct": promoter_pct,
        "promoter_holding_qoq_change": _round2(promoter_pct - previous_promoter)
        if promoter_pct is not None and previous_promoter is not None
        else None,
        "shareholding_history": shareholding_history,
    }


def _build_technicals(
    *, cmp: float | None, nse_price_info: dict, nse_pre_open: dict, nse_dp: dict, nse_trade_data: dict
) -> dict:
    vwap = _safe_float(nse_price_info.get("vwap"))
    iep = _safe_float(nse_pre_open.get("IEP"))
    prev_close = _safe_float(nse_pre_open.get("prevClose"))
    delivery_pct = _safe_float(nse_dp.get("deliveryToTradedQuantity"))
    lower_cp = _safe_float(str(nse_price_info.get("lowerCP") or "").replace(",", "").strip())
    upper_cp = _safe_float(str(nse_price_info.get("upperCP") or "").replace(",", "").strip())
    return {
        "vwap_signal_pct": _round2((cmp / vwap - 1) * 100) if cmp is not None and vwap not in (None, 0) else None,
        "pre_open_sentiment_pct": _round2((iep / prev_close - 1) * 100)
        if iep is not None and prev_close not in (None, 0)
        else None,
        "price_band_type": str(nse_price_info.get("pPriceBand") or "").strip() or None,
        "dist_from_lower_circuit_pct": _round2((cmp / lower_cp - 1) * 100)
        if cmp is not None and lower_cp not in (None, 0)
        else None,
        "dist_from_upper_circuit_pct": _round2((upper_cp / cmp - 1) * 100)
        if cmp is not None and upper_cp not in (None, 0)
        else None,
        "delivery_signal": "High"
        if delivery_pct is not None and delivery_pct >= 50
        else "Medium"
        if delivery_pct is not None and delivery_pct >= 30
        else "Low"
        if delivery_pct is not None
        else None,
    }
