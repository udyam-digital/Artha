from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import httpx
from anthropic import APIConnectionError, APIStatusError, RateLimitError

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")


@dataclass
class RetryFailure(RuntimeError):
    phase: str
    retries_used: int
    cause: Exception
    ticker: str | None = None
    transient: bool = False
    partial_artifact_path: Path | None = None

    def __post_init__(self) -> None:
        super().__init__(str(self.cause))


@dataclass
class FullRunFailed(RuntimeError):
    phase: str
    message: str
    retries_used: int
    ticker: str | None = None
    error_log_path: Path | None = None
    partial_artifact_path: Path | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)


def is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, asyncio.TimeoutError | TimeoutError | httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, APIConnectionError | RateLimitError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (status_code == 429 or status_code >= 500):
        return True
    message = str(exc).lower()
    transient_terms = (
        "timeout",
        "temporar",
        "connection reset",
        "connection aborted",
        "rate limit",
        "try again",
        "server error",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
    )
    return any(term in message for term in transient_terms)


async def run_with_retries(
    operation: Callable[[], Awaitable[ResultT]],
    *,
    attempts: int,
    base_delay_seconds: float,
    phase: str,
    ticker: str | None = None,
    partial_artifact_path: Path | None = None,
) -> ResultT:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            last_exc = exc
            transient = is_transient_error(exc)
            retries_used = attempt - 1
            if not transient or attempt >= attempts:
                raise RetryFailure(
                    phase=phase,
                    ticker=ticker,
                    retries_used=retries_used,
                    cause=exc,
                    transient=transient,
                    partial_artifact_path=partial_artifact_path,
                ) from exc
            delay = base_delay_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Transient failure in %s%s on attempt %s/%s: %s. Retrying in %.1fs",
                phase,
                f" for {ticker}" if ticker else "",
                attempt,
                attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    raise AssertionError(f"unreachable retry loop state: {last_exc}")  # pragma: no cover
