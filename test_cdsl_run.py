import asyncio
import json
from pathlib import Path

from anthropic import AsyncAnthropic

from analysis.analyst import generate_company_artifact
from config import get_settings
from kite.tools import get_macro_context, get_yfinance_snapshot
from models import Holding


async def main():
    config = get_settings()
    client = AsyncAnthropic(api_key=config.anthropic_api_key)
    holding = Holding(
        tradingsymbol="CDSL",
        exchange="NSE",
        quantity=10,
        average_price=1500.0,
        last_price=1190.8,
        current_value=11908.0,
        current_weight_pct=5.0,
        target_weight_pct=5.0,
        pnl=-3092.0,
        pnl_pct=-20.6,
        instrument_token=0,
    )
    skills_content = (Path("skills") / "analyst_prompt.md").read_text()
    snap = await get_yfinance_snapshot("CDSL")
    price_context = {
        "52w_high": snap.get("fifty_two_week_high") or 2000.0,
        "52w_low": snap.get("fifty_two_week_low") or 1000.0,
        "current_vs_52w_high_pct": snap.get("upside_pct") or -10.0,
        "price_1y_ago": 1600.0,
        "price_change_1y_pct": -12.0,
    }
    macro_ctx = await get_macro_context()
    macro_str = (
        f"CPI: {macro_ctx.cpi_headline_yoy}%, IIP: {macro_ctx.iip_growth_latest}%, GDP: {macro_ctx.gdp_growth_latest}%"
    )
    artifact = await generate_company_artifact(
        holding=holding,
        price_context=price_context,
        macro_context=macro_str,
        skills_content=skills_content,
        client=client,
        config=config,
    )
    card = artifact.model_dump(mode="json")
    print("=== VERDICT ===")
    print(json.dumps(card["analysis"]["final_verdict"], indent=2))
    print("\n=== KEY QUALITY FIELDS (should be Python-overwritten) ===")
    print("roe:", card["analysis"]["quality"]["roe"])
    print("roce:", card["analysis"]["quality"]["roce"])
    print("pe:", card["analysis"]["valuation"]["pe"])
    print("sector_pe:", card["analysis"]["valuation"]["sector_pe"])
    print("revenue_cagr:", card["analysis"]["growth_engine"]["revenue_cagr"])
    print("eps_cagr:", card["analysis"]["growth_engine"]["eps_cagr"])
    print("price_vs_200dma:", card["analysis"]["timing"]["price_vs_200dma"])
    print("fair_value_range:", card["analysis"]["valuation"]["fair_value_range"])
    print("margin_of_safety:", card["analysis"]["valuation"]["margin_of_safety"])
    print("\n=== SOURCE MAP ===")
    print(json.dumps(card["analysis"]["source_map"], indent=2))


asyncio.run(main())
