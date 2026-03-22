from __future__ import annotations

import pytest

import kite.tools as kite_tools
from models import MacroContext

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clear_macro_cache():
    kite_tools._MACRO_CONTEXT_CACHE.clear()


async def test_get_macro_context_returns_model(monkeypatch) -> None:
    async def fake_cpi(client):
        return 4.5, "2026-03"

    async def fake_iip(client):
        return 3.2, "2026-03"

    async def fake_gdp(client):
        return 6.4, "2026-03"

    monkeypatch.setattr(kite_tools, "_fetch_cpi_context", fake_cpi)
    monkeypatch.setattr(kite_tools, "_fetch_iip_context", fake_iip)
    monkeypatch.setattr(kite_tools, "_fetch_gdp_context", fake_gdp)

    result = await kite_tools.get_macro_context()

    assert isinstance(result, MacroContext)


async def test_macro_context_partial_failure(monkeypatch) -> None:
    async def fake_cpi(client):
        raise RuntimeError("cpi unavailable")

    async def fake_iip(client):
        return 3.2, "2026-03"

    async def fake_gdp(client):
        return 6.4, "2026-03"

    monkeypatch.setattr(kite_tools, "_fetch_cpi_context", fake_cpi)
    monkeypatch.setattr(kite_tools, "_fetch_iip_context", fake_iip)
    monkeypatch.setattr(kite_tools, "_fetch_gdp_context", fake_gdp)

    result = await kite_tools.get_macro_context()

    assert result.iip_growth_latest == 3.2
    assert result.gdp_growth_latest == 6.4
    assert result.fetch_errors


async def test_macro_context_caching(monkeypatch) -> None:
    calls = {"count": 0}

    async def fake_cpi(client):
        calls["count"] += 1
        return 4.5, "2026-03"

    async def fake_iip(client):
        calls["count"] += 1
        return 3.2, "2026-03"

    async def fake_gdp(client):
        calls["count"] += 1
        return 6.4, "2026-03"

    monkeypatch.setattr(kite_tools, "_fetch_cpi_context", fake_cpi)
    monkeypatch.setattr(kite_tools, "_fetch_iip_context", fake_iip)
    monkeypatch.setattr(kite_tools, "_fetch_gdp_context", fake_gdp)

    first = await kite_tools.get_macro_context()
    second = await kite_tools.get_macro_context()

    assert first == second
    assert calls["count"] == 3
