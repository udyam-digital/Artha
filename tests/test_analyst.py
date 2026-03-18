from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Settings
from analyst import analyse_stock
from models import Holding


pytestmark = pytest.mark.anyio


class FakeAnthropicClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)

    @property
    def messages(self):
        return SimpleNamespace(create=self.messages_create)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
        MODEL="claude-sonnet-4-6",
    )


def make_holding() -> Holding:
    return Holding(
        tradingsymbol="KPITTECH",
        exchange="NSE",
        quantity=10,
        average_price=100.0,
        last_price=80.0,
        current_value=800.0,
        current_weight_pct=5.0,
        target_weight_pct=7.0,
        pnl=-200.0,
        pnl_pct=-20.0,
        instrument_token=123,
    )


def make_report_card_code(ticker: str, name: str = "KPIT Technologies", final_verdict: str = "ADD") -> str:
    return f"""
output = {{
    "stock_snapshot": {{
        "name": "{name}",
        "ticker": "{ticker}",
        "sector": "Technology",
        "market_cap_category": "Mid",
        "52w_high": 1928.0,
        "52w_low": 980.0,
        "current_price": 1420.0,
        "time_horizon": "Compounder"
    }},
    "thesis": {{
        "core_idea": "Engineering-led software franchise.",
        "growth_driver": "Auto software demand remains healthy.",
        "edge": "Deep domain capability.",
        "trigger": "Large deal pipeline conversion"
    }},
    "growth_engine": {{
        "revenue_cagr": "24%",
        "eps_cagr": "22%",
        "sector_tailwind": "High",
        "growth_score": 8
    }},
    "quality": {{
        "roce": "28%",
        "roe": "24%",
        "debt_to_equity": "0.02",
        "fcf_status": "Positive",
        "governance_flags": "None identified",
        "quality_score": 8
    }},
    "valuation": {{
        "pe": "52x",
        "sector_pe": "48x",
        "peg": "2.1",
        "fcf_yield": "1.8%",
        "fair_value_range": [1300, 1500],
        "margin_of_safety": "Limited",
        "rvs_score": 6
    }},
    "timing": {{
        "price_vs_200dma": "+6%",
        "momentum": "Neutral",
        "fii_trend": "Stable",
        "timing_signal": "Neutral"
    }},
    "capital_efficiency": {{
        "roic_trend": "Improving",
        "reinvestment_quality": "Disciplined",
        "capital_efficiency_score": 8
    }},
    "risk_matrix": {{
        "structural_risks": ["Auto program delays"],
        "cyclical_risks": ["Global auto slowdown"],
        "company_risks": ["Execution slippage"],
        "risk_level": "Medium"
    }},
    "action_plan": {{
        "buy_zone": [1250, 1350],
        "add_zone": 1380,
        "hold_zone": "1350-1550",
        "trim_zone": 1650,
        "stop_loss": 1180
    }},
    "position_sizing": {{
        "suggested_allocation": "5-6%",
        "max_allocation": "8%"
    }},
    "final_verdict": {{
        "verdict": "{final_verdict}",
        "confidence": "High"
    }},
    "monitoring": {{
        "next_triggers": ["Quarterly margin trajectory"],
        "key_metrics": ["Large-deal wins"],
        "red_flags": ["Client concentration rise"]
    }}
}}
"""


async def test_analyse_stock_parses_tool_use_then_end_turn(tmp_path: Path) -> None:
    tool_use_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="tool_use", id="tool-1", name="web_search", input={"query": "KPIT results"})
        ],
    )
    final_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(
                type="text",
                text=make_report_card_code("KPITTECH"),
            )
        ],
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={"52w_high": 100.0, "52w_low": 60.0, "current_vs_52w_high_pct": -20.0},
        skills_content="system",
        client=FakeAnthropicClient([tool_use_response, final_response]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    monkeypatch.undo()
    assert verdict.verdict == "BUY"
    assert verdict.current_price == 1420.0
    assert verdict.error is None
    assert (tmp_path / "data" / "companies" / "KPITTECH.json").exists()


async def test_analyse_stock_falls_back_without_tags(tmp_path: Path) -> None:
    response = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="invalid")])
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=FakeAnthropicClient([response]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None


async def test_analyse_stock_falls_back_on_invalid_json(tmp_path: Path) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="<verdict>{not-json}</verdict>")],
    )
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=FakeAnthropicClient([response]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None


async def test_analyse_stock_falls_back_on_invalid_schema(tmp_path: Path) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(
                type="text",
                text='<verdict>{"tradingsymbol":"KPITTECH","company_name":"KPIT Tech"}</verdict>',
            )
        ],
    )
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=FakeAnthropicClient([response]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None


async def test_analyse_stock_supports_standalone_mode(tmp_path: Path) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(
                type="text",
                text=make_report_card_code("INFY", name="Infosys", final_verdict="HOLD"),
            )
        ],
    )
    holding = Holding(
        tradingsymbol="INFY",
        exchange="NSE",
        quantity=0,
        average_price=0.0,
        last_price=0.0,
        current_value=0.0,
        current_weight_pct=0.0,
        target_weight_pct=0.0,
        pnl=0.0,
        pnl_pct=0.0,
        instrument_token=0,
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    verdict = await analyse_stock(
        holding=holding,
        portfolio_total_value=0.0,
        price_context={},
        skills_content="system",
        client=FakeAnthropicClient([response]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    monkeypatch.undo()
    assert verdict.tradingsymbol == "INFY"
    assert verdict.current_price == 1420.0
    assert verdict.error is None
    assert (tmp_path / "data" / "companies" / "INFY.json").exists()
