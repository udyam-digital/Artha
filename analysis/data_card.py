from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _safe_float(value: Any) -> float | None:
    """Return float or None on None/empty string/NaN."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _round2(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return _round2((new - old) / abs(old) * 100)


def _parse_shareholding_history(sh_data: Any) -> list[dict]:
    """Parse shareholding_patterns.data dict (keyed by date string) into list of dicts."""
    if not isinstance(sh_data, dict):
        return []
    history: list[dict] = []
    for date_str, entries in sh_data.items():
        if not isinstance(entries, list):
            continue
        record: dict[str, Any] = {"date": date_str}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for k, v in entry.items():
                try:
                    record[k] = float(str(v).strip())
                except (TypeError, ValueError):
                    record[k] = v
        history.append(record)
    # Sort chronologically (oldest first)
    try:
        from datetime import datetime as _dt
        history.sort(key=lambda r: _dt.strptime(str(r["date"]), "%d-%b-%Y"))
    except Exception:
        pass
    return history


def _get_promoter_pct(entries: list[Any]) -> float | None:
    """Extract Promoter & Promoter Group % from a list of shareholding entries."""
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for k, v in entry.items():
            if "promoter" in k.lower():
                return _safe_float(str(v).strip())
    return None


def build_company_data_card(
    *,
    ticker: str,
    exchange: str,
    yf_raw: dict,
    nse_raw: dict,
    price_context: dict,
) -> dict:
    """
    Build all data-only sections of CompanyDataCard from raw provider payloads.
    Returns a dict with keys: meta, price_data, valuation, financials,
    nse_quarterly, quality, ownership, dividends_corporate, technical_signals.
    The 'analysis' key is NOT populated here — the LLM fills that.
    """
    now = datetime.now(timezone.utc)

    # ── NSE sub-dicts ──────────────────────────────────────────────────────────
    details: dict = nse_raw.get("details") or {}
    trade_info: dict = nse_raw.get("trade_info") or {}
    corporate_info: dict = nse_raw.get("corporate_info") or {}

    nse_info: dict = details.get("info") or {} if isinstance(details, dict) else {}
    nse_metadata: dict = details.get("metadata") or {} if isinstance(details, dict) else {}
    nse_security_info: dict = details.get("securityInfo") or {} if isinstance(details, dict) else {}
    nse_price_info: dict = details.get("priceInfo") or {} if isinstance(details, dict) else {}
    nse_industry_info: dict = details.get("industryInfo") or {} if isinstance(details, dict) else {}
    nse_pre_open: dict = details.get("preOpenMarket") or {} if isinstance(details, dict) else {}

    nse_market_book: dict = trade_info.get("marketDeptOrderBook") or {} if isinstance(trade_info, dict) else {}
    nse_trade_data: dict = nse_market_book.get("tradeInfo") or {} if isinstance(nse_market_book, dict) else {}
    nse_var: dict = nse_market_book.get("valueAtRisk") or {} if isinstance(nse_market_book, dict) else {}
    nse_dp: dict = trade_info.get("securityWiseDP") or {} if isinstance(trade_info, dict) else {}

    nse_financial_results: list = (
        corporate_info.get("financial_results", {}).get("data") or []
        if isinstance(corporate_info, dict)
        else []
    )
    nse_shareholdings: dict = (
        corporate_info.get("shareholdings_patterns", {}).get("data") or {}
        if isinstance(corporate_info, dict)
        else {}
    )
    nse_corp_actions: list = (
        corporate_info.get("corporate_actions", {}).get("data") or []
        if isinstance(corporate_info, dict)
        else []
    )

    # ── Derived scalar helpers ─────────────────────────────────────────────────
    cmp = _safe_float(yf_raw.get("currentPrice") or yf_raw.get("regularMarketPrice"))
    trailing_eps = _safe_float(yf_raw.get("trailingEps") or yf_raw.get("epsTrailingTwelveMonths"))
    book_value = _safe_float(yf_raw.get("bookValue"))
    ev = _safe_float(yf_raw.get("enterpriseValue"))
    ev_ebitda_ratio = _safe_float(yf_raw.get("enterpriseToEbitda"))
    ebitda_val = _safe_float(yf_raw.get("ebitda"))
    total_cash = _safe_float(yf_raw.get("totalCash"))
    total_debt = _safe_float(yf_raw.get("totalDebt"))

    # ── 1. CardMeta ────────────────────────────────────────────────────────────
    surv_obj = nse_security_info.get("surveillance") or {}
    surv_value = surv_obj.get("surv", "") if isinstance(surv_obj, dict) else ""
    is_under_surveillance = bool(surv_value and str(surv_value).strip() not in ("", "-", "None"))
    surveillance_stage = str(surv_obj.get("desc", "")).strip() or None if is_under_surveillance else None

    index_memberships: list[str] = []
    sector_ind_all = nse_metadata.get("pdSectorIndAll")
    if isinstance(sector_ind_all, list):
        index_memberships = [str(x) for x in sector_ind_all if x]
    elif isinstance(sector_ind_all, str) and sector_ind_all:
        index_memberships = [sector_ind_all]

    meta = {
        "ticker": ticker.upper(),
        "exchange": exchange.upper(),
        "isin": nse_info.get("isin") or nse_metadata.get("isin"),
        "company_name": (
            nse_info.get("companyName")
            or nse_info.get("symbol")
            or ticker.upper()
        ),
        "listing_date": (
            nse_info.get("listingDate")
            or nse_metadata.get("listingDate")
        ),
        "face_value": _safe_float(nse_security_info.get("faceValue")),
        "sector": nse_industry_info.get("sector") or nse_industry_info.get("macro"),
        "industry": nse_industry_info.get("industry"),
        "industry_macro": nse_industry_info.get("macro"),
        "industry_4l": nse_industry_info.get("basicIndustry"),
        "index_memberships": index_memberships,
        "is_fno": bool(nse_info.get("isFNOSec", False)),
        "is_slb": bool(nse_info.get("isSLBSec", False)),
        "is_under_surveillance": is_under_surveillance,
        "surveillance_stage": surveillance_stage,
        "fetched_at": now,
    }

    # ── 2. CardPriceData ───────────────────────────────────────────────────────
    vwap = _safe_float(nse_price_info.get("vwap"))
    week_hl = nse_price_info.get("weekHighLow") or {}
    week_52_high = _safe_float(week_hl.get("max") if isinstance(week_hl, dict) else None)
    week_52_low = _safe_float(week_hl.get("min") if isinstance(week_hl, dict) else None)
    week_52_high_date = str(week_hl.get("maxDate", "")).strip() or None if isinstance(week_hl, dict) else None
    week_52_low_date = str(week_hl.get("minDate", "")).strip() or None if isinstance(week_hl, dict) else None

    dma_50 = _safe_float(yf_raw.get("fiftyDayAverage"))
    dma_200 = _safe_float(yf_raw.get("twoHundredDayAverage"))

    vs_52w_high_pct = _safe_float(price_context.get("current_vs_52w_high_pct"))

    vs_50dma_pct: float | None = None
    if cmp is not None and dma_50 is not None and dma_50 != 0:
        vs_50dma_pct = _round2((cmp / dma_50 - 1) * 100)

    vs_200dma_pct: float | None = None
    if cmp is not None and dma_200 is not None and dma_200 != 0:
        vs_200dma_pct = _round2((cmp / dma_200 - 1) * 100)

    chg_52w = _safe_float(yf_raw.get("52WeekChange"))
    sp_52w = _safe_float(yf_raw.get("SandP52WeekChange"))
    alpha_vs_nifty: float | None = None
    if chg_52w is not None and sp_52w is not None:
        alpha_vs_nifty = _round2((chg_52w - sp_52w) * 100)

    annual_vol = _safe_float(nse_trade_data.get("cmAnnualVolatility"))

    price_data = {
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
        "week_52_high": week_52_high,
        "week_52_low": week_52_low,
        "week_52_high_date": week_52_high_date,
        "week_52_low_date": week_52_low_date,
        "vs_52w_high_pct": vs_52w_high_pct,
        "dma_50": dma_50,
        "dma_200": dma_200,
        "vs_50dma_pct": vs_50dma_pct,
        "vs_200dma_pct": vs_200dma_pct,
        "alpha_vs_nifty_52w_pct": alpha_vs_nifty,
        "beta": _safe_float(yf_raw.get("beta")),
        "annual_volatility_pct": _safe_float(annual_vol),
    }

    # ── 3. CardValuation ──────────────────────────────────────────────────────
    trailing_pe = _safe_float(yf_raw.get("trailingPE"))
    sector_pe = _safe_float(nse_metadata.get("pdSectorPe"))

    pe_premium: float | None = None
    if trailing_pe is not None and sector_pe is not None and sector_pe != 0:
        pe_premium = _round2((trailing_pe / sector_pe - 1) * 100)

    earnings_yield: float | None = None
    if trailing_eps is not None and cmp is not None and cmp != 0:
        earnings_yield = _round2((trailing_eps / cmp) * 100)

    analyst_upside = _safe_float(yf_raw.get("targetMeanPrice"))
    analyst_upside_pct: float | None = None
    if analyst_upside is not None and cmp is not None and cmp != 0:
        analyst_upside_pct = _round2((analyst_upside / cmp - 1) * 100)

    valuation = {
        "trailing_pe": trailing_pe,
        "forward_pe": _safe_float(yf_raw.get("forwardPE")),
        "sector_pe": sector_pe,
        "pe_premium_to_sector_pct": pe_premium,
        "price_to_book": _safe_float(yf_raw.get("priceToBook")),
        "price_to_sales": _safe_float(yf_raw.get("priceToSalesTrailing12Months")),
        "ev_ebitda": ev_ebitda_ratio,
        "ev_revenue": _safe_float(yf_raw.get("enterpriseToRevenue")),
        "peg_ratio": _safe_float(yf_raw.get("trailingPegRatio")),
        "earnings_yield_pct": earnings_yield,
        "analyst_target_mean": _safe_float(yf_raw.get("targetMeanPrice")),
        "analyst_target_median": _safe_float(yf_raw.get("targetMedianPrice")),
        "analyst_upside_pct": analyst_upside_pct,
        "analyst_count": _safe_int(yf_raw.get("numberOfAnalystOpinions")),
        "analyst_consensus": str(yf_raw.get("recommendationKey", "") or "").strip() or None,
    }

    # ── 4. CardFinancials ──────────────────────────────────────────────────────
    # NSE market cap is in crores — convert to rupees (* 1e7)
    nse_mkt_cap_crore = _safe_float(nse_trade_data.get("totalMarketCap"))
    market_cap = _round2(nse_mkt_cap_crore * 1e7) if nse_mkt_cap_crore is not None else _safe_float(yf_raw.get("marketCap"))
    ffmc_crore = _safe_float(nse_trade_data.get("ffmc"))
    free_float_market_cap = _round2(ffmc_crore * 1e7) if ffmc_crore is not None else None

    net_cash: float | None = None
    if total_cash is not None and total_debt is not None:
        net_cash = _round2(total_cash - total_debt)

    def _margin_pct(key: str) -> float | None:
        v = _safe_float(yf_raw.get(key))
        if v is None:
            return None
        return _round2(v * 100)

    financials = {
        "market_cap": market_cap,
        "free_float_market_cap": free_float_market_cap,
        "enterprise_value": _safe_float(yf_raw.get("enterpriseValue")),
        "total_revenue": _safe_float(yf_raw.get("totalRevenue")),
        "revenue_per_share": _safe_float(yf_raw.get("revenuePerShare")),
        "gross_profit": _safe_float(yf_raw.get("grossProfits")),
        "gross_margin_pct": _margin_pct("grossMargins"),
        "ebitda": ebitda_val,
        "ebitda_margin_pct": _margin_pct("ebitdaMargins"),
        "operating_margin_pct": _margin_pct("operatingMargins"),
        "net_income": _safe_float(yf_raw.get("netIncomeToCommon")),
        "profit_margin_pct": _margin_pct("profitMargins"),
        "trailing_eps": trailing_eps,
        "forward_eps": _safe_float(yf_raw.get("forwardEps") or yf_raw.get("epsForward")),
        "total_cash": total_cash,
        "total_debt": total_debt,
        "net_cash": net_cash,
        "debt_to_equity": _safe_float(yf_raw.get("debtToEquity")),
        "revenue_growth_pct": _round2((_safe_float(yf_raw.get("revenueGrowth")) or 0) * 100) if _safe_float(yf_raw.get("revenueGrowth")) is not None else None,
        "earnings_growth_pct": _round2((_safe_float(yf_raw.get("earningsGrowth")) or 0) * 100) if _safe_float(yf_raw.get("earningsGrowth")) is not None else None,
        "book_value_per_share": book_value,
    }

    # ── 5. CardNSEQuarterly ────────────────────────────────────────────────────
    quarters_parsed: list[dict] = []
    for entry in (nse_financial_results if isinstance(nse_financial_results, list) else []):
        if not isinstance(entry, dict):
            continue
        period = str(entry.get("to_date") or entry.get("from_date") or "").strip()
        if not period:
            continue
        quarters_parsed.append({
            "period": period,
            "_sort_key": period,
            "income": _safe_float(entry.get("income")),
            "pat": _safe_float(entry.get("proLossAftTax")),
            "eps": _safe_float(entry.get("reDilEPS")),
            "pbt": _safe_float(entry.get("reProLossBefTax")),
            "audited": str(entry.get("audited") or "").strip() or None,
        })

    # Sort by date descending (most recent first) — try parsing "31 Dec 2025" format
    def _sort_quarter(q: dict) -> str:
        try:
            from datetime import datetime as _dt
            return _dt.strptime(q["_sort_key"], "%d %b %Y").strftime("%Y%m%d")
        except Exception:
            return q["_sort_key"]

    quarters_parsed.sort(key=_sort_quarter, reverse=True)

    # Remove the internal sort key
    quarters_clean = [
        {k: v for k, v in q.items() if k != "_sort_key"}
        for q in quarters_parsed
    ]

    revenue_qoq_pct: float | None = None
    revenue_yoy_pct: float | None = None
    eps_qoq_pct: float | None = None
    pat_qoq_pct: float | None = None

    if len(quarters_clean) >= 2:
        revenue_qoq_pct = _pct_change(quarters_clean[0]["income"], quarters_clean[1]["income"])
        eps_qoq_pct = _pct_change(quarters_clean[0]["eps"], quarters_clean[1]["eps"])
        pat_qoq_pct = _pct_change(quarters_clean[0]["pat"], quarters_clean[1]["pat"])
    if len(quarters_clean) >= 5:
        revenue_yoy_pct = _pct_change(quarters_clean[0]["income"], quarters_clean[4]["income"])

    nse_quarterly = {
        "quarters": quarters_clean,
        "revenue_qoq_pct": revenue_qoq_pct,
        "revenue_yoy_pct": revenue_yoy_pct,
        "eps_qoq_pct": eps_qoq_pct,
        "pat_qoq_pct": pat_qoq_pct,
    }

    # ── 6. CardQuality ─────────────────────────────────────────────────────────
    roe_proxy: float | None = None
    if trailing_eps is not None and book_value is not None and book_value != 0:
        roe_proxy = _round2((trailing_eps / book_value) * 100)

    # ROCE = net_income / capital_employed * 100
    # capital_employed = (book_value_per_share * shares_outstanding) + total_debt
    net_income_val = _safe_float(yf_raw.get("netIncomeToCommon"))
    shares_outstanding_q = _safe_float(yf_raw.get("sharesOutstanding") or yf_raw.get("impliedSharesOutstanding"))
    roce_proxy: float | None = None
    if (
        net_income_val is not None
        and book_value is not None
        and shares_outstanding_q is not None
        and shares_outstanding_q != 0
    ):
        equity_capital = book_value * shares_outstanding_q
        debt_for_roce = total_debt if total_debt is not None else 0.0
        capital_employed = equity_capital + debt_for_roce
        if capital_employed != 0:
            roce_proxy = _round2((net_income_val / capital_employed) * 100)

    delivery_pct = _safe_float(nse_dp.get("deliveryToTradedQuantity"))
    impact_cost = _safe_float(nse_trade_data.get("impactCost"))
    var_margin = _safe_float(nse_var.get("applicableMargin"))

    audit_risk = _safe_int(yf_raw.get("auditRisk"))
    board_risk = _safe_int(yf_raw.get("boardRisk"))
    comp_risk = _safe_int(yf_raw.get("compensationRisk"))
    overall_risk = _safe_int(yf_raw.get("overallRisk"))

    gov_values = [v for v in [audit_risk, board_risk, comp_risk, overall_risk] if v is not None]
    governance_score = _round2(sum(gov_values) / len(gov_values)) if gov_values else None

    quality = {
        "roe_proxy_pct": roe_proxy,
        "roce_proxy_pct": roce_proxy,
        "delivery_pct": delivery_pct,
        "impact_cost": impact_cost,
        "var_applicable_margin_pct": var_margin,
        "governance_score": governance_score,
        "audit_risk": audit_risk,
        "board_risk": board_risk,
        "compensation_risk": comp_risk,
        "overall_risk_score": overall_risk,
    }

    # ── 7. CardOwnership ──────────────────────────────────────────────────────
    shares_outstanding = _safe_float(yf_raw.get("sharesOutstanding") or yf_raw.get("impliedSharesOutstanding"))
    float_shares = _safe_float(yf_raw.get("floatShares"))
    float_ratio_pct: float | None = None
    if float_shares is not None and shares_outstanding is not None and shares_outstanding != 0:
        float_ratio_pct = _round2((float_shares / shares_outstanding) * 100)

    sh_history = _parse_shareholding_history(nse_shareholdings)

    promoter_pct: float | None = None
    promoter_qoq: float | None = None
    if sh_history:
        # Most recent entry is last after sort (oldest first), reversed for latest
        latest_sh = sh_history[-1]
        promoter_pct = _safe_float(latest_sh.get("Promoter & Promoter Group"))
        if len(sh_history) >= 2:
            prev_sh = sh_history[-2]
            prev_promoter = _safe_float(prev_sh.get("Promoter & Promoter Group"))
            if promoter_pct is not None and prev_promoter is not None:
                promoter_qoq = _round2(promoter_pct - prev_promoter)

    inst_pct_raw = _safe_float(yf_raw.get("heldPercentInstitutions"))
    insider_pct_raw = _safe_float(yf_raw.get("heldPercentInsiders"))

    ownership = {
        "shares_outstanding": shares_outstanding,
        "float_shares": float_shares,
        "float_ratio_pct": float_ratio_pct,
        "institutional_holding_pct": _round2(inst_pct_raw * 100) if inst_pct_raw is not None else None,
        "insider_holding_pct": _round2(insider_pct_raw * 100) if insider_pct_raw is not None else None,
        "promoter_holding_pct": promoter_pct,
        "promoter_holding_qoq_change": promoter_qoq,
        "shareholding_history": sh_history,
    }

    # ── 8. CardDividendCorporate ───────────────────────────────────────────────
    # yfinance dividendYield is already in percentage (e.g. 0.21 = 0.21% not 21%)
    div_yield_raw = _safe_float(yf_raw.get("dividendYield"))
    payout_raw = _safe_float(yf_raw.get("payoutRatio"))

    # Epoch → date string
    def _epoch_to_date(epoch: Any) -> str | None:
        v = _safe_float(epoch)
        if v is None:
            return None
        try:
            from datetime import datetime as _dt
            return _dt.utcfromtimestamp(v).strftime("%Y-%m-%d")
        except Exception:
            return None

    last_div_date = _epoch_to_date(yf_raw.get("lastDividendDate"))
    next_earnings_date = _epoch_to_date(yf_raw.get("earningsTimestampStart"))

    recent_actions = [
        dict(entry) for entry in (nse_corp_actions[:5] if isinstance(nse_corp_actions, list) else [])
        if isinstance(entry, dict)
    ]

    dividends_corporate = {
        # dividendYield from yfinance is already a percentage value (e.g. 0.21 = 0.21%, not 21%)
        "dividend_yield_pct": div_yield_raw,
        "payout_ratio": payout_raw,
        "five_yr_avg_dividend_yield": _safe_float(yf_raw.get("fiveYearAvgDividendYield")),
        "last_dividend_amount": _safe_float(yf_raw.get("lastDividendValue")),
        "last_dividend_date": last_div_date,
        "next_earnings_date": next_earnings_date,
        "recent_corporate_actions": recent_actions,
    }

    # ── 9. CardTechnicals ─────────────────────────────────────────────────────
    vwap_signal: float | None = None
    if cmp is not None and vwap is not None and vwap != 0:
        vwap_signal = _round2((cmp / vwap - 1) * 100)

    iep = _safe_float(nse_pre_open.get("IEP"))
    prev_close_po = _safe_float(nse_pre_open.get("prevClose"))
    pre_open_sent: float | None = None
    if iep is not None and prev_close_po is not None and prev_close_po != 0:
        pre_open_sent = _round2((iep / prev_close_po - 1) * 100)

    lower_cp_str = str(nse_price_info.get("lowerCP") or "").replace(",", "").strip()
    upper_cp_str = str(nse_price_info.get("upperCP") or "").replace(",", "").strip()
    lower_cp = _safe_float(lower_cp_str)
    upper_cp = _safe_float(upper_cp_str)

    dist_lower: float | None = None
    if cmp is not None and lower_cp is not None and lower_cp != 0:
        dist_lower = _round2((cmp / lower_cp - 1) * 100)

    dist_upper: float | None = None
    if cmp is not None and upper_cp is not None and upper_cp != 0:
        dist_upper = _round2((upper_cp / cmp - 1) * 100)

    price_band_type = str(nse_price_info.get("pPriceBand") or "").strip() or None

    delivery_signal: str | None = None
    if delivery_pct is not None:
        if delivery_pct >= 50:
            delivery_signal = "High"
        elif delivery_pct >= 30:
            delivery_signal = "Medium"
        else:
            delivery_signal = "Low"

    technical_signals = {
        "vwap_signal_pct": vwap_signal,
        "pre_open_sentiment_pct": pre_open_sent,
        "price_band_type": price_band_type,
        "dist_from_lower_circuit_pct": dist_lower,
        "dist_from_upper_circuit_pct": dist_upper,
        "delivery_signal": delivery_signal,
    }

    return {
        "meta": meta,
        "price_data": price_data,
        "valuation": valuation,
        "financials": financials,
        "nse_quarterly": nse_quarterly,
        "quality": quality,
        "ownership": ownership,
        "dividends_corporate": dividends_corporate,
        "technical_signals": technical_signals,
    }
