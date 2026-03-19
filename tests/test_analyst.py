from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis.analyst import analyse_stock
from config import Settings
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
        ANALYST_MODEL="claude-haiku-4-5",
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


def make_report_card_json(ticker: str, name: str = "KPIT Technologies", final_verdict: str = "ADD") -> str:
    return f"""{{
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
    }},
    "data_sources": [
        "https://www.screener.in/company/{ticker}/",
        "https://www.example.com/{ticker.lower()}-results"
    ]
}}"""


async def test_analyse_stock_parses_tool_use_then_end_turn(tmp_path: Path) -> None:
    tool_use_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="tool_use", id="tool-1", name="tavily_search", input={"query": "KPIT results"})
        ],
    )
    final_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(
                type="text",
                text=make_report_card_json("KPITTECH"),
            )
        ],
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("analysis.analyst.tavily_search", lambda **kwargs: "Summary: KPIT result")
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
    assert verdict.current_price == 80.0
    assert verdict.error is None
    assert len(verdict.data_sources) == 2
    assert (tmp_path / "kite" / "companies" / "KPITTECH.json").exists()
    assert "tavily_search" == tool_use_response.content[0].name


async def test_analyse_stock_uses_analyst_model(tmp_path: Path) -> None:
    client = FakeAnthropicClient(
        [
            SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=make_report_card_json("KPITTECH"))],
            )
        ]
    )
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=client,  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert client.calls[0]["model"] == "claude-haiku-4-5"


async def test_analyse_stock_sends_minimal_portfolio_context(tmp_path: Path) -> None:
    client = FakeAnthropicClient(
        [
            SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=make_report_card_json("KPITTECH"))],
            )
        ]
    )
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={
            "52w_high": 100.0,
            "52w_low": 60.0,
            "current_vs_52w_high_pct": -20.0,
            "price_1y_ago": 70.0,
            "price_change_1y_pct": 14.0,
            "candles": [1, 2, 3],
        },
        skills_content="system",
        client=client,  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    prompt = client.calls[0]["messages"][0]["content"]
    assert '"tradingsymbol":"KPITTECH"' in prompt
    assert '"exchange":"NSE"' in prompt
    assert '"quantity":10' in prompt
    assert '"drift":-2.0' in prompt
    assert '"52w_high":100.0' in prompt
    assert '"52w_low":60.0' in prompt
    assert '"current_vs_52w_high_pct":-20.0' in prompt
    assert "current_value" not in prompt
    assert "price_1y_ago" not in prompt
    assert "price_change_1y_pct" not in prompt
    assert "candles" not in prompt
    assert "latest quarterly results FY25" not in prompt
    assert "management commentary 2025" not in prompt
    assert "latest quarterly results" in prompt


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
        content=[SimpleNamespace(type="text", text="{not-json}")],
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
                text='{"tradingsymbol":"KPITTECH","company_name":"KPIT Tech"}',
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
                text=make_report_card_json("INFY", name="Infosys", final_verdict="HOLD"),
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
    assert len(verdict.data_sources) == 2
    assert (tmp_path / "kite" / "companies" / "INFY.json").exists()


async def test_analyse_stock_rejects_legacy_python_payload(tmp_path: Path) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="output = {'stock_snapshot': {}}")],
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


async def test_analyse_stock_extracts_json_from_wrapped_text(tmp_path: Path) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(
                type="text",
                text=f"Here is the report card you requested:\n```json\n{make_report_card_json('KPITTECH')}\n```",
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
    assert verdict.verdict == "BUY"
    assert verdict.error is None


async def test_analyse_stock_repairs_invalid_json_with_followup_turn(tmp_path: Path) -> None:
    invalid_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="I found the right data. Returning it now.")],
    )
    fixed_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=make_report_card_json("KPITTECH"))],
    )
    client = FakeAnthropicClient([invalid_response, fixed_response])
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=client,  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "BUY"
    assert verdict.error is None
    assert len(client.calls) == 2


async def test_analyse_stock_enforces_tavily_search_budget(tmp_path: Path) -> None:
    tool_uses = [
        SimpleNamespace(type="tool_use", id=f"tool-{index}", name="tavily_search", input={"query": f"KPIT {index}"})
        for index in range(1, 5)
    ]
    tool_use_response = SimpleNamespace(stop_reason="tool_use", content=tool_uses)
    final_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=make_report_card_json("KPITTECH"))],
    )
    client = FakeAnthropicClient([tool_use_response, final_response])
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("analysis.analyst.tavily_search", lambda **kwargs: f"Summary: {kwargs['query']}")
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=client,  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    tool_results = client.calls[1]["messages"][-1]["content"]
    assert len(tool_results) == 4
    assert tool_results[-1]["is_error"] is True
    assert "budget exhausted" in tool_results[-1]["content"]
    monkeypatch.undo()
