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
                text=(
                    '<verdict>{"tradingsymbol":"KPITTECH","company_name":"KPIT Tech","verdict":"BUY",'
                    '"confidence":"HIGH","thesis_intact":true,"bull_case":"Execution remains strong.",'
                    '"bear_case":"Auto cycle could soften.","what_to_watch":"Deal wins","red_flags":[],'
                    '"rebalance_action":"BUY","rebalance_rupees":5000,'
                    '"rebalance_reasoning":"Drift and intact thesis support adding.",'
                    '"data_sources":["https://example.com"]}</verdict>'
                ),
            )
        ],
    )
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={"52w_high": 100.0, "52w_low": 60.0, "current_vs_52w_high_pct": -20.0},
        skills_content="system",
        client=FakeAnthropicClient([tool_use_response, final_response]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.verdict == "BUY"
    assert verdict.current_price == 80.0
    assert verdict.error is None


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
                text=(
                    '<verdict>{"tradingsymbol":"INFY","company_name":"Infosys","verdict":"HOLD",'
                    '"confidence":"MEDIUM","thesis_intact":true,"bull_case":"Cash generation remains solid.",'
                    '"bear_case":"Growth may stay muted.","what_to_watch":"Large-deal TCV","red_flags":[],'
                    '"rebalance_action":"HOLD","rebalance_rupees":0,'
                    '"rebalance_reasoning":"Standalone research only.","data_sources":["https://example.com"]}</verdict>'
                ),
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
    verdict = await analyse_stock(
        holding=holding,
        portfolio_total_value=0.0,
        price_context={},
        skills_content="system",
        client=FakeAnthropicClient([response]),  # type: ignore[arg-type]
        config=make_settings(tmp_path),
    )
    assert verdict.tradingsymbol == "INFY"
    assert verdict.current_price == 0.0
    assert verdict.error is None
