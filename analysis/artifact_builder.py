from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from analysis.fiscal import get_fiscal_context
from config import Settings
from models import AnalystReportCard, CompanyAnalysisArtifact, CompanyDataCard, Holding


def _overwrite_report_card_with_data_card(
    report_card: AnalystReportCard,
    data_card_sections: dict,
) -> tuple[AnalystReportCard, dict[str, str]]:
    """Overwrite specific string fields in AnalystReportCard with exact data card values.
    Returns updated report_card and a dict of what was overwritten (for the judge)."""
    overwritten: dict[str, str] = {}

    # From quality section
    roe = data_card_sections.get("quality", {}).get("roe_proxy_pct")
    if roe is not None:
        report_card.quality.roe = f"{roe:.1f}% (yfinance API)"
        overwritten["quality.roe"] = report_card.quality.roe

    roce = data_card_sections.get("quality", {}).get("roce_proxy_pct")
    if roce is not None:
        report_card.quality.roce = f"{roce:.1f}% (yfinance API)"
        overwritten["quality.roce"] = report_card.quality.roce

    de = data_card_sections.get("financials", {}).get("debt_to_equity")
    if de is not None:
        report_card.quality.debt_to_equity = f"{de:.2f}x (yfinance API)"
        overwritten["quality.debt_to_equity"] = report_card.quality.debt_to_equity

    # From valuation section
    pe = data_card_sections.get("valuation", {}).get("trailing_pe")
    if pe is not None:
        report_card.valuation.pe = f"{pe:.1f}x TTM (yfinance API)"
        overwritten["valuation.pe"] = report_card.valuation.pe

    sector_pe = data_card_sections.get("valuation", {}).get("sector_pe")
    if sector_pe is not None:
        report_card.valuation.sector_pe = f"{sector_pe:.1f}x (NSE India API)"
        overwritten["valuation.sector_pe"] = report_card.valuation.sector_pe

    peg = data_card_sections.get("valuation", {}).get("peg_ratio")
    if peg is not None:
        report_card.valuation.peg = f"{peg:.2f} (yfinance API)"
        overwritten["valuation.peg"] = report_card.valuation.peg

    # From nse_quarterly — revenue_cagr and eps_cagr
    fiscal = get_fiscal_context()
    nse_q = data_card_sections.get("nse_quarterly", {})
    rev_yoy = nse_q.get("revenue_yoy_pct")
    if rev_yoy is not None:
        report_card.growth_engine.revenue_cagr = f"{rev_yoy:.1f}% YoY ({fiscal['latest_quarter']}) (NSE India API)"
        overwritten["growth_engine.revenue_cagr"] = report_card.growth_engine.revenue_cagr

    # EPS: use latest quarter EPS from quarters list
    quarters = nse_q.get("quarters", [])
    latest_eps = quarters[0].get("eps") if quarters else None
    eps_qoq = nse_q.get("eps_qoq_pct")
    if latest_eps is not None and eps_qoq is not None:
        report_card.growth_engine.eps_cagr = f"₹{latest_eps:.2f} EPS latest quarter, {eps_qoq:.1f}% QoQ (NSE India API)"
        overwritten["growth_engine.eps_cagr"] = report_card.growth_engine.eps_cagr
    elif latest_eps is not None:
        report_card.growth_engine.eps_cagr = f"₹{latest_eps:.2f} EPS latest quarter (NSE India API)"
        overwritten["growth_engine.eps_cagr"] = report_card.growth_engine.eps_cagr

    # From price_data — timing
    vs_200dma = data_card_sections.get("price_data", {}).get("vs_200dma_pct")
    if vs_200dma is not None:
        direction = "above" if vs_200dma > 0 else "below"
        report_card.timing.price_vs_200dma = f"{vs_200dma:.1f}% {direction} 200 DMA (yfinance API)"
        overwritten["timing.price_vs_200dma"] = report_card.timing.price_vs_200dma

    # FII trend from delivery_pct + institutional_holding
    delivery_pct = data_card_sections.get("quality", {}).get("delivery_pct")
    inst_pct = data_card_sections.get("ownership", {}).get("institutional_holding_pct")
    if delivery_pct is not None:
        signal = data_card_sections.get("technical_signals", {}).get("delivery_signal", "Medium")
        inst_str = f", institutional holding {inst_pct:.1f}%" if inst_pct is not None else ""
        report_card.timing.fii_trend = f"Delivery {delivery_pct:.1f}% ({signal} conviction){inst_str} (NSE India API)"
        overwritten["timing.fii_trend"] = report_card.timing.fii_trend

    return report_card, overwritten


def _compute_fair_value(data_card_sections: dict) -> list[float] | None:
    """Compute fair value range from Python-verified data card values.
    Returns [low, high] or None if insufficient data."""
    valuation = data_card_sections.get("valuation", {})
    financials = data_card_sections.get("financials", {})

    forward_eps = financials.get("forward_eps") or financials.get("trailing_eps")
    trailing_pe = valuation.get("trailing_pe")
    sector_pe = valuation.get("sector_pe")
    analyst_target = valuation.get("analyst_target_mean")

    if not forward_eps or forward_eps <= 0:
        return None

    # Base PE = lower of trailing PE and sector PE (conservative anchor)
    base_pe = None
    if trailing_pe and sector_pe:
        base_pe = min(trailing_pe, sector_pe * 1.1)  # cap at 10% sector premium
    elif sector_pe:
        base_pe = sector_pe
    elif trailing_pe:
        base_pe = trailing_pe

    if not base_pe or base_pe <= 0:
        return None

    fair_mid = round(forward_eps * base_pe, 1)
    fair_low = round(fair_mid * 0.85, 1)
    fair_high = round(max(fair_mid * 1.15, analyst_target or 0), 1)

    return [fair_low, fair_high]


def _fix_internal_consistency(report_card: AnalystReportCard) -> AnalystReportCard:
    """Deterministic post-processing to fix common LLM internal inconsistencies."""
    # 1. Recalculate margin_of_safety from fair_value_range and current_price
    fv = report_card.valuation.fair_value_range
    price = report_card.stock_snapshot.current_price
    if len(fv) == 2 and fv[0] > 0 and fv[1] > 0 and price > 0:
        midpoint = (fv[0] + fv[1]) / 2
        mos_pct = (midpoint - price) / price * 100
        if mos_pct >= 0:
            report_card.valuation.margin_of_safety = f"+{mos_pct:.1f}% (discount)"
        else:
            report_card.valuation.margin_of_safety = f"{mos_pct:.1f}% (overvalued)"

    # 2. Fix action_plan zone ordering: stop_loss < buy_zone[0] <= buy_zone[1] < add_zone < trim_zone
    ap = report_card.action_plan
    if len(ap.buy_zone) == 2 and ap.buy_zone[0] > ap.buy_zone[1]:
        ap.buy_zone = [ap.buy_zone[1], ap.buy_zone[0]]
    if len(ap.buy_zone) == 2:
        if ap.stop_loss >= ap.buy_zone[0] and ap.buy_zone[0] > 0:
            ap.stop_loss = round(ap.buy_zone[0] * 0.90, 1)  # 10% below low buy
        if ap.add_zone <= ap.buy_zone[1] and ap.buy_zone[1] > 0:
            ap.add_zone = round(ap.buy_zone[1] * 1.05, 1)  # 5% above high buy
        if ap.trim_zone <= ap.add_zone and ap.add_zone > 0:
            ap.trim_zone = round(ap.add_zone * 1.15, 1)  # 15% above add

    return report_card


def _build_company_artifact(
    *,
    report_card: AnalystReportCard,
    holding: Holding,
    config: Settings,
    yfinance_data: dict[str, object] | None = None,
) -> CompanyAnalysisArtifact:
    return CompanyAnalysisArtifact(
        generated_at=datetime.now(UTC),
        source_model=config.analyst_model,
        exchange=holding.exchange,
        ticker=holding.tradingsymbol.upper(),
        report_card=report_card,
        yfinance_data=yfinance_data or {},
    )


def _build_company_data_card_artifact(
    *,
    report_card: AnalystReportCard,
    holding: Holding,
    config: Settings,
    data_card_sections: dict[str, Any],
) -> CompanyDataCard:
    return CompanyDataCard(
        generated_at=datetime.now(UTC),
        source_model=config.analyst_model,
        exchange=holding.exchange,
        ticker=holding.tradingsymbol.upper(),
        analysis=report_card,
        **data_card_sections,
    )
