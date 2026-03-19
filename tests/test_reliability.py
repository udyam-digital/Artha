from __future__ import annotations

import asyncio

import httpx
import pytest

import reliability
from reliability import FullRunFailed, RetryFailure, is_transient_error, run_with_retries


pytestmark = pytest.mark.anyio


async def test_run_with_retries_retries_transient_failure_then_succeeds() -> None:
    state = {"calls": 0}

    async def operation() -> str:
        state["calls"] += 1
        if state["calls"] == 1:
            raise httpx.TimeoutException("timeout")
        return "ok"

    result = await run_with_retries(
        operation,
        attempts=3,
        base_delay_seconds=0.0,
        phase="kite_sync",
    )
    assert result == "ok"
    assert state["calls"] == 2


async def test_run_with_retries_fails_fast_for_non_transient_error() -> None:
    async def operation() -> str:
        raise ValueError("bad input")

    with pytest.raises(RetryFailure) as exc_info:
        await run_with_retries(
            operation,
            attempts=3,
            base_delay_seconds=0.0,
            phase="analyst",
            ticker="KPITTECH",
        )
    assert exc_info.value.retries_used == 0
    assert exc_info.value.transient is False
    assert exc_info.value.ticker == "KPITTECH"


async def test_run_with_retries_requires_positive_attempts() -> None:
    async def operation() -> str:
        return "ok"

    with pytest.raises(ValueError):
        await run_with_retries(operation, attempts=0, base_delay_seconds=0.0, phase="kite_sync")


def test_is_transient_error_detects_common_cases() -> None:
    assert is_transient_error(asyncio.TimeoutError()) is True
    assert is_transient_error(httpx.TransportError("connection reset")) is True
    assert is_transient_error(RuntimeError("service unavailable")) is True
    assert is_transient_error(ValueError("validation failed")) is False


def test_is_transient_error_detects_api_specific_classes(monkeypatch) -> None:
    class FakeConnectionError(Exception):
        pass

    class FakeRateLimitError(Exception):
        pass

    class FakeApiStatusError(Exception):
        def __init__(self, status_code: int):
            self.status_code = status_code

    monkeypatch.setattr(reliability, "APIConnectionError", FakeConnectionError)
    monkeypatch.setattr(reliability, "RateLimitError", FakeRateLimitError)
    monkeypatch.setattr(reliability, "APIStatusError", FakeApiStatusError)

    assert is_transient_error(FakeConnectionError()) is True
    assert is_transient_error(FakeRateLimitError()) is True
    assert is_transient_error(FakeApiStatusError(429)) is True


def test_is_transient_error_detects_status_code_attribute() -> None:
    class HasStatusCode(Exception):
        def __init__(self, status_code: int):
            self.status_code = status_code

    assert is_transient_error(HasStatusCode(503)) is True


def test_full_run_failed_uses_message_as_exception_text() -> None:
    exc = FullRunFailed(phase="analyst", message="boom", retries_used=1)
    assert str(exc) == "boom"
