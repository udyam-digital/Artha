from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import application.orchestrator as orchestrator
from config import Settings
from models import Holding, MacroContext

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def mock_macro_context(monkeypatch):
    async def fake_macro_context():
        return MacroContext(
            cpi_headline_yoy=4.5, iip_growth_latest=3.2, gdp_growth_latest=6.4, as_of_date="2026-03", fetch_errors=[]
        )

    monkeypatch.setattr(orchestrator, "get_macro_context", fake_macro_context)


class FakeSummaryClient:
    def __init__(self) -> None:
        self.calls = []
        self.count_calls = []

    async def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Portfolio summary")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    async def messages_count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(input_tokens=222)

    @property
    def messages(self):
        return SimpleNamespace(create=self.messages_create, count_tokens=self.messages_count_tokens)


class FakeKiteClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def make_settings(tmp_path: Path, **overrides) -> Settings:
    payload = {
        "ANTHROPIC_API_KEY": "test-key",  # pragma: allowlist secret
        "REPORTS_DIR": str(tmp_path / "reports"),
        "KITE_DATA_DIR": str(tmp_path / "kite"),
        "MODEL": "claude-sonnet-4-6",
        "ANALYST_MODEL": "claude-haiku-4-5",
        "ANALYST_PARALLELISM": 1,
        "ANALYST_MIN_START_INTERVAL_SECONDS": 0,
    }
    payload.update(overrides)
    return Settings(**payload)


def make_holding(symbol: str, current_weight: float, target_weight: float) -> Holding:
    return Holding(
        tradingsymbol=symbol,
        exchange="NSE",
        quantity=10,
        average_price=100.0,
        last_price=100.0,
        current_value=1000.0,
        current_weight_pct=current_weight,
        target_weight_pct=target_weight,
        pnl=100.0,
        pnl_pct=10.0,
        instrument_token=100 + len(symbol),
    )
