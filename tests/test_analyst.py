from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from analysis.analyst import analyse_stock
from config import Settings
from models import AnalystReportCard, Holding


pytestmark = pytest.mark.anyio


PASSING_QUALITY_SCORES = {
    "recency": 70,
    "risk_completeness": 65,
    "valuation_accuracy": 60,
    "verdict_logic": 70,
    "overall": 67,
    "key_issues": [],
    "one_line_summary": "Good quality report",
}

PASSING_FACTUAL_SCORES = {
    "source_grounding": 70,
    "hallucination_risk": 80,
    "data_consistency": 65,
    "overall": 72,
    "red_flags": [],
    "one_line_summary": "Well-grounded report",
}


@pytest.fixture(autouse=True)
def _mock_judges():
    """Mock both judge functions and data provider functions so tests don't need extra LLM responses."""
    with (
        patch("analysis.analyst.judge_report_card", new_callable=AsyncMock, return_value=PASSING_QUALITY_SCORES),
        patch("analysis.analyst.judge_factual_grounding", new_callable=AsyncMock, return_value=PASSING_FACTUAL_SCORES),
        patch(
            "analysis.analyst.get_yfinance_snapshot",
            new_callable=AsyncMock,
            return_value={"ticker": "KPITTECH.NS", "cmp": 80.0, "upside_pct": 10.0},
        ),
        patch(
            "analysis.analyst.get_yfinance_company_info",
            new_callable=AsyncMock,
            return_value={"currentPrice": 80.0, "trailingPE": 25.0},
        ),
        patch(
            "analysis.analyst.get_nse_india_provider_payload",
            new_callable=AsyncMock,
            return_value={"raw": {}, "snapshot": {}, "errors": []},
        ),
    ):
        yield


class FakeAnthropicClient:
    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []
        self.last_response: Any = None

    async def messages_count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(input_tokens=111)

    async def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        self.last_response = response
        return response

    async def messages_create_with_completion(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0) if self._responses else self.last_response
        if isinstance(response, Exception):
            raise response
        payload = getattr(response, "payload", response)
        return kwargs["response_model"].model_validate(payload), response

    @property
    def messages(self):
        return SimpleNamespace(
            create=self.messages_create,
            create_with_completion=self.messages_create_with_completion,
            count_tokens=self.messages_count_tokens,
        )


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


def make_report_card_payload(ticker: str, name: str = "KPIT Technologies", final_verdict: str = "ADD") -> dict[str, Any]:
    return {
        "stock_snapshot": {
            "name": name,
            "ticker": ticker,
            "sector": "Technology",
            "market_cap_category": "Mid",
            "52w_high": 1928.0,
            "52w_low": 980.0,
            "current_price": 1420.0,
            "time_horizon": "Compounder",
        },
        "thesis": {
            "core_idea": "Engineering-led software franchise.",
            "growth_driver": "Auto software demand remains healthy.",
            "edge": "Deep domain capability.",
            "trigger": "Large deal pipeline conversion",
        },
        "growth_engine": {
            "revenue_cagr": "24%",
            "eps_cagr": "22%",
            "sector_tailwind": "High",
            "growth_score": 8,
        },
        "quality": {
            "roce": "28%",
            "roe": "24%",
            "debt_to_equity": "0.02",
            "fcf_status": "Positive",
            "governance_flags": "None identified",
            "quality_score": 8,
        },
        "valuation": {
            "pe": "52x",
            "sector_pe": "48x",
            "peg": "2.1",
            "fcf_yield": "1.8%",
            "fair_value_range": [1300, 1500],
            "margin_of_safety": "Limited",
            "rvs_score": 6,
        },
        "timing": {
            "price_vs_200dma": "+6%",
            "momentum": "Neutral",
            "fii_trend": "Stable",
            "timing_signal": "Neutral",
        },
        "capital_efficiency": {
            "roic_trend": "Improving",
            "reinvestment_quality": "Disciplined",
            "capital_efficiency_score": 8,
        },
        "risk_matrix": {
            "structural_risks": ["Auto program delays"],
            "cyclical_risks": ["Global auto slowdown"],
            "company_risks": ["Execution slippage"],
            "risk_level": "Medium",
        },
        "action_plan": {
            "buy_zone": [1250, 1350],
            "add_zone": 1380,
            "hold_zone": "1350-1550",
            "trim_zone": 1650,
            "stop_loss": 1180,
        },
        "position_sizing": {
            "suggested_allocation": "5-6%",
            "max_allocation": "8%",
        },
        "final_verdict": {
            "verdict": final_verdict,
            "confidence": "High",
        },
        "monitoring": {
            "next_triggers": ["Quarterly margin trajectory"],
            "key_metrics": ["Large-deal wins"],
            "red_flags": ["Client concentration rise"],
        },
        "data_sources": [
            f"https://www.screener.in/company/{ticker}/",
            f"https://www.example.com/{ticker.lower()}-results",
        ],
        "source_map": {
            "revenue_cagr": f"https://www.screener.in/company/{ticker}/",
            "roce": f"https://www.screener.in/company/{ticker}/",
            "pe": f"https://www.example.com/{ticker.lower()}-results",
        },
    }


def make_final_response(ticker: str = "KPITTECH", name: str = "KPIT Technologies", final_verdict: str = "ADD"):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="structured response")],
        payload=make_report_card_payload(ticker, name=name, final_verdict=final_verdict),
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


async def test_analyse_stock_parses_tool_use_then_end_turn(tmp_path: Path) -> None:
    tool_use_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="tool_use", id="tool-1", name="tavily_search", input={"query": "KPIT results"})
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    final_response = make_final_response("KPITTECH")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("analysis.analyst.tavily_search", lambda **kwargs: "Summary: KPIT result")
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={"52w_high": 100.0, "52w_low": 60.0, "current_vs_52w_high_pct": -20.0},
        macro_context="",
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
    client = FakeAnthropicClient([make_final_response("KPITTECH")])
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        macro_context="",
        skills_content="system",
        client=client,  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert client.calls[0]["model"] == "claude-haiku-4-5"


async def test_analyse_stock_sends_minimal_portfolio_context(tmp_path: Path) -> None:
    client = FakeAnthropicClient([make_final_response("KPITTECH")])
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
        macro_context="Macro (as of 2026-03): CPI 4.50% | IIP growth 3.20% | GDP growth 6.40%",
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
    assert '"macro_context":"Macro (as of 2026-03): CPI 4.50% | IIP growth 3.20% | GDP growth 6.40%"' in prompt
    assert '"yfinance_data":{"ticker":"KPITTECH.NS","cmp":80.0,"upside_pct":10.0}' in prompt
    assert "current_value" not in prompt
    assert "price_1y_ago" not in prompt
    assert "price_change_1y_pct" not in prompt
    assert "candles" not in prompt
    # Old stale query phrases must be gone
    assert "FY25" not in prompt
    assert "2025" not in prompt
    # New prompt must reference the current fiscal period and research tasks
    assert "quarterly results" in prompt
    assert "data_sources" in prompt


async def test_analyse_stock_falls_back_without_tags(tmp_path: Path) -> None:
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        macro_context="",
        skills_content="system",
        client=FakeAnthropicClient([ValueError("invalid")]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None


async def test_analyse_stock_falls_back_on_invalid_json(tmp_path: Path) -> None:
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        macro_context="",
        skills_content="system",
        client=FakeAnthropicClient([ValueError("{not-json}")]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None


async def test_analyse_stock_falls_back_on_invalid_schema(tmp_path: Path) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="partial structured response")],
        payload={"tradingsymbol": "KPITTECH", "company_name": "KPIT Tech"},
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        macro_context="",
        skills_content="system",
        client=FakeAnthropicClient([response]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None


async def test_analyse_stock_supports_standalone_mode(tmp_path: Path) -> None:
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
        macro_context="",
        skills_content="system",
        client=FakeAnthropicClient([make_final_response("INFY", name="Infosys", final_verdict="HOLD")]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    monkeypatch.undo()
    assert verdict.tradingsymbol == "INFY"
    assert verdict.current_price == 1420.0
    assert verdict.error is None
    assert len(verdict.data_sources) == 2
    assert (tmp_path / "kite" / "companies" / "INFY.json").exists()


async def test_analyse_stock_rejects_legacy_python_payload(tmp_path: Path) -> None:
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        macro_context="",
        skills_content="system",
        client=FakeAnthropicClient([ValueError("output = {'stock_snapshot': {}}")]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None


async def test_analyse_stock_returns_fallback_on_instructor_validation_error(tmp_path: Path) -> None:
    client = FakeAnthropicClient([make_final_response("KPITTECH"), ValueError("validation failed")])
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        macro_context="",
        skills_content="system",
        client=client,  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None
    assert len(client.calls) == 2


async def test_analyse_stock_enforces_tavily_search_budget(tmp_path: Path) -> None:
    tool_uses = [
        SimpleNamespace(type="tool_use", id=f"tool-{index}", name="tavily_search", input={"query": f"KPIT {index}"})
        for index in range(1, 5)
    ]
    tool_use_response = SimpleNamespace(
        stop_reason="tool_use",
        content=tool_uses,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    final_response = make_final_response("KPITTECH")
    client = FakeAnthropicClient([tool_use_response, final_response])
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("analysis.analyst.tavily_search", lambda **kwargs: f"Summary: {kwargs['query']}")
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        macro_context="",
        skills_content="system",
        client=client,  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    tool_results = client.calls[1]["messages"][-1]["content"]
    assert len(tool_results) == 4
    assert tool_results[-1]["is_error"] is True
    assert "budget exhausted" in tool_results[-1]["content"]
    monkeypatch.undo()


async def test_analyse_stock_retries_on_low_judge_score(tmp_path: Path) -> None:
    """When combined judge score is below threshold, analyst retries and produces a new artifact."""
    low_quality = {**PASSING_QUALITY_SCORES, "overall": 20, "key_issues": ["Stale data"]}
    low_factual = {**PASSING_FACTUAL_SCORES, "overall": 15, "red_flags": ["No real sources"]}
    call_count = 0

    async def _mock_quality_judge(*args, **kwargs):
        nonlocal call_count
        # First call returns low score, second call returns passing score
        if call_count == 0:
            return low_quality
        return PASSING_QUALITY_SCORES

    async def _mock_factual_judge(*args, **kwargs):
        nonlocal call_count
        if call_count == 0:
            return low_factual
        return PASSING_FACTUAL_SCORES

    # We need 2 end_turn responses (first attempt + retry) and 2 instructor responses
    client = FakeAnthropicClient([
        make_final_response("KPITTECH"),  # first analyst run
        make_final_response("KPITTECH"),  # first instructor coerce
        make_final_response("KPITTECH"),  # retry analyst run
        make_final_response("KPITTECH"),  # retry instructor coerce
    ])

    settings = make_settings(tmp_path)
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
        MODEL="claude-sonnet-4-6",
        ANALYST_MODEL="claude-haiku-4-5",
        JUDGE_RETRY_THRESHOLD=45,
        JUDGE_MAX_RETRIES=1,
    )

    # Track when the quality judge is called to flip scores on retry
    original_count = 0

    async def _quality_side_effect(*args, **kwargs):
        nonlocal original_count
        original_count += 1
        if original_count == 1:
            return low_quality
        return PASSING_QUALITY_SCORES

    async def _factual_side_effect(*args, **kwargs):
        if original_count <= 1:
            return low_factual
        return PASSING_FACTUAL_SCORES

    with (
        patch("analysis.analyst.judge_report_card", side_effect=_quality_side_effect),
        patch("analysis.analyst.judge_factual_grounding", side_effect=_factual_side_effect),
    ):
        verdict = await analyse_stock(
            holding=make_holding(),
            portfolio_total_value=10_000.0,
            price_context={},
            macro_context="",
            skills_content="system",
            client=client,  # type: ignore[arg-type]
            config=settings,
        )

    assert verdict.verdict == "BUY"
    assert verdict.error is None
    # Should have called the analyst LLM twice (original + retry), plus instructor twice
    assert len(client.calls) == 4
    # Judge scores file should exist
    assert (tmp_path / "kite" / "companies" / "KPITTECH_judge.json").exists()


async def test_analyse_stock_persists_judge_scores(tmp_path: Path) -> None:
    """Judge scores are persisted to {TICKER}_judge.json."""
    client = FakeAnthropicClient([make_final_response("KPITTECH")])
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        macro_context="",
        skills_content="system",
        client=client,  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    judge_path = tmp_path / "kite" / "companies" / "KPITTECH_judge.json"
    assert judge_path.exists()
    import json
    scores = json.loads(judge_path.read_text())
    assert scores["ticker"] == "KPITTECH"
    assert scores["quality_scores"] is not None
    assert scores["factual_scores"] is not None
    assert scores["combined_overall"] > 0
    assert scores["passed"] is True
