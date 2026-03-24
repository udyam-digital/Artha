from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from config import Settings
from models import Holding

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
def mock_judges_and_providers():
    with (
        patch(
            "analysis.analyst_runtime.judge_report_card", new_callable=AsyncMock, return_value=PASSING_QUALITY_SCORES
        ),
        patch(
            "analysis.analyst_runtime.judge_factual_grounding",
            new_callable=AsyncMock,
            return_value=PASSING_FACTUAL_SCORES,
        ),
        patch(
            "analysis.analyst_runtime.get_yfinance_company_info",
            new_callable=AsyncMock,
            return_value={
                "currentPrice": 80.0,
                "trailingPE": 25.0,
                "targetMeanPrice": 88.0,
                "numberOfAnalystOpinions": 5,
            },
        ),
        patch(
            "analysis.analyst_runtime.get_nse_india_provider_payload",
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
        ANTHROPIC_API_KEY="test-key",  # pragma: allowlist secret
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


def make_report_card_payload(
    ticker: str, name: str = "KPIT Technologies", final_verdict: str = "ADD"
) -> dict[str, Any]:
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
        "growth_engine": {"revenue_cagr": "24%", "eps_cagr": "22%", "sector_tailwind": "High", "growth_score": 8},
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
        "timing": {"price_vs_200dma": "+6%", "momentum": "Neutral", "fii_trend": "Stable", "timing_signal": "Neutral"},
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
        "position_sizing": {"suggested_allocation": "5-6%", "max_allocation": "8%"},
        "final_verdict": {"verdict": final_verdict, "confidence": "High"},
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
