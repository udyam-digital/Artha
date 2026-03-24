from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis.analyst import analyse_stock
from models import Holding
from tests.analyst_support import (
    FakeAnthropicClient,
    make_final_response,
    make_holding,
    make_settings,
    mock_judges_and_providers,  # noqa: F401
)

pytestmark = pytest.mark.anyio


async def test_analyse_stock_parses_tool_use_then_end_turn(tmp_path: Path) -> None:
    tool_use_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="tool-1", name="tavily_search", input={"query": "KPIT results"})],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    final_response = make_final_response("KPITTECH")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("analysis.tool_router.tavily_search", lambda **kwargs: "Summary: KPIT result")
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={"52w_high": 100.0, "52w_low": 60.0, "current_vs_52w_high_pct": -20.0},
        skills_content="system",
        client=FakeAnthropicClient([tool_use_response, final_response]),
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
    monkeypatch.undo()
    assert verdict.verdict == "BUY"
    assert verdict.current_price == 80.0
    assert verdict.error is None
    assert len(verdict.data_sources) == 2
    assert (tmp_path / "kite" / "companies" / "KPITTECH.json").exists()


async def test_analyse_stock_uses_analyst_model(tmp_path: Path) -> None:
    client = FakeAnthropicClient([make_final_response("KPITTECH")])
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=client,
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
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
        skills_content="system",
        client=client,
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
    prompt = client.calls[0]["messages"][0]["content"]
    assert '"tradingsymbol":"KPITTECH"' in prompt
    assert '"exchange":"NSE"' in prompt
    assert '"quantity":10' in prompt
    assert '"drift":-2.0' in prompt
    assert '"52w_high":100.0' in prompt
    assert '"52w_low":60.0' in prompt
    assert '"current_vs_52w_high_pct":-20.0' in prompt
    assert '"yfinance_data":{' in prompt
    assert '"ticker":"KPITTECH.NS"' in prompt
    assert '"cmp":80.0' in prompt
    assert "current_value" not in prompt
    assert "price_1y_ago" not in prompt
    assert "price_change_1y_pct" not in prompt
    assert "candles" not in prompt
    assert "FY25" not in prompt
    assert "2025" not in prompt
    assert "quarterly results" in prompt
    assert "data_sources" in prompt


@pytest.mark.parametrize(
    "response", [ValueError("invalid"), ValueError("{not-json}"), ValueError("output = {'stock_snapshot': {}}")]
)
async def test_analyse_stock_fallback_on_bad_unstructured_output(tmp_path: Path, response: Exception) -> None:
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=FakeAnthropicClient([response]),
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
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
        skills_content="system",
        client=FakeAnthropicClient([response]),
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
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
        skills_content="system",
        client=FakeAnthropicClient([make_final_response("INFY", name="Infosys", final_verdict="HOLD")]),
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
    monkeypatch.undo()
    assert verdict.tradingsymbol == "INFY"
    assert verdict.current_price == 1420.0
    assert verdict.error is None
    assert len(verdict.data_sources) == 2
    assert (tmp_path / "kite" / "companies" / "INFY.json").exists()


async def test_analyse_stock_returns_fallback_on_instructor_validation_error(tmp_path: Path) -> None:
    client = FakeAnthropicClient([make_final_response("KPITTECH"), ValueError("validation failed")])
    verdict = await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=client,
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
    assert verdict.verdict == "HOLD"
    assert verdict.error is not None
    assert len(client.calls) == 2


async def test_analyse_stock_enforces_tavily_search_budget(tmp_path: Path) -> None:
    tool_uses = [
        SimpleNamespace(type="tool_use", id=f"tool-{index}", name="tavily_search", input={"query": f"KPIT {index}"})
        for index in range(1, 5)
    ]
    client = FakeAnthropicClient(
        [
            SimpleNamespace(
                stop_reason="tool_use", content=tool_uses, usage=SimpleNamespace(input_tokens=10, output_tokens=5)
            ),
            make_final_response("KPITTECH"),
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("analysis.tool_router.tavily_search", lambda **kwargs: f"Summary: {kwargs['query']}")
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=client,
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
    tool_results = client.calls[1]["messages"][-1]["content"]
    assert len(tool_results) == 4
    assert tool_results[-1]["is_error"] is True
    assert "budget exhausted" in tool_results[-1]["content"]
    monkeypatch.undo()
