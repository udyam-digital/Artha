import asyncio, json
from pathlib import Path
from anthropic import AsyncAnthropic
from analysis.analyst import generate_company_artifact
from config import get_settings
from models import Holding
from kite.tools import get_yfinance_snapshot, get_macro_context

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
    macro_str = f"CPI: {macro_ctx.cpi_headline_yoy}%, IIP: {macro_ctx.iip_growth_latest}%, GDP: {macro_ctx.gdp_growth_latest}%"
    artifact = await generate_company_artifact(
        holding=holding,
        price_context=price_context,
        macro_context=macro_str,
        skills_content=skills_content,
        client=client,
        config=config,
    )
    print("=== CDSL CompanyDataCard ===")
    card_dict = artifact.model_dump(mode="json")
    # Print key sections
    print(f"VERDICT: {card_dict['analysis']['final_verdict']}")
    print(f"PRICE DATA: {json.dumps(card_dict['price_data'], indent=2)}")
    print(f"VALUATION: {json.dumps(card_dict['valuation'], indent=2)}")
    print(f"QUALITY: {json.dumps(card_dict['quality'], indent=2)}")
    print(f"NSE QUARTERLY: {json.dumps(card_dict['nse_quarterly'], indent=2)}")
    print(f"SOURCE MAP: {json.dumps(card_dict['analysis']['source_map'], indent=2)}")
    print(f"DATA SOURCES: {json.dumps(card_dict['analysis']['data_sources'], indent=2)}")

asyncio.run(main())
