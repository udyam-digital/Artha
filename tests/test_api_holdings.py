from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main
from tests.api_support import build_cached_holdings, make_settings, parse_sse_stream


def test_holdings_returns_401_with_login_url(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    build_cached_holdings(settings)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(api_main, "build_kite_client", lambda settings: FakeClient())

    async def fake_profile(_kite_client):
        return {}

    async def fake_login(_kite_client, settings):
        return ({}, "https://kite.example/login", settings.kite_data_dir / "auth.json")

    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "kite_login", fake_login)
    payload = TestClient(api_main.create_app()).get("/api/holdings").json()
    assert payload["live_status"] == "fallback"
    assert payload["live_error"]["login_url"] == "https://kite.example/login"
    assert payload["holdings"][0]["tradingsymbol"] == "KPITTECH"


def test_holdings_without_cache_still_requires_login(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(api_main, "build_kite_client", lambda settings: FakeClient())

    async def fake_profile(_kite_client):
        return {}

    async def fake_login(_kite_client, settings):
        return ({}, "https://kite.example/login", settings.kite_data_dir / "auth.json")

    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "kite_login", fake_login)
    response = TestClient(api_main.create_app()).get("/api/holdings")
    assert response.status_code == 401
    assert response.json()["detail"]["login_url"] == "https://kite.example/login"


def test_holdings_does_not_mask_unexpected_exceptions(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def broken_portfolio(*args, **kwargs):
        raise ValueError("unexpected bug")

    monkeypatch.setattr(api_main, "build_kite_client", lambda settings: FakeClient())

    async def fake_profile(_kite_client):
        return {"user_name": "ok"}

    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "kite_get_portfolio", broken_portfolio)
    assert TestClient(api_main.create_app(), raise_server_exceptions=False).get("/api/holdings").status_code == 500


def test_stream_run_sanitizes_rebalance_only_errors(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    async def fake_sync_kite_data(*, settings):
        raise RuntimeError("failed to reach https://internal.example/path")

    monkeypatch.setattr(api_main, "sync_kite_data", fake_sync_kite_data)

    async def consume_events() -> list[str]:
        events: list[str] = []
        async for event in api_main._stream_run(api_main.RunRequest(rebalance_only=True), settings):  # noqa: SLF001
            events.append(event)
        return events

    error_event = [
        event
        for event in parse_sse_stream("".join(asyncio.run(consume_events())).encode("utf-8"))
        if event["event"] == "error"
    ][0]
    assert error_event["data"]["message"] == "Rebalance-only run failed."
    assert error_event["data"]["error_code"] == api_main.STREAM_ERROR_CODE
    assert "internal.example" not in json.dumps(error_event["data"])


def test_stream_run_cancels_background_task_when_closed(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    task_cancelled = asyncio.Event()
    task_started = asyncio.Event()

    async def fake_run_and_save(request, settings, event_callback):
        del request, settings
        event_callback({"type": "phase", "phase": "analyst", "label": "Running", "total": 1})
        task_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            task_cancelled.set()
            raise

    monkeypatch.setattr(api_main, "_run_and_save", fake_run_and_save)

    async def exercise_stream() -> None:
        stream = api_main._stream_run(api_main.RunRequest(), settings)  # noqa: SLF001
        await stream.__anext__()
        await stream.__anext__()
        await task_started.wait()
        await stream.aclose()
        assert task_cancelled.is_set()

    asyncio.run(exercise_stream())
