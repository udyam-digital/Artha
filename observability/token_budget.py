from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TokenBudgetManager:
    """
    Sliding-window rate limiter for Anthropic API token budgets.
    Tracks usage over a rolling 60-second window and delays callers
    if the budget would be exceeded.
    """

    input_tokens_per_minute: int
    output_tokens_per_minute: int
    window_seconds: float = 60.0
    _input_log: list[tuple[float, int]] = field(default_factory=list)
    _output_log: list[tuple[float, int]] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _prune(self, log: list[tuple[float, int]], now: float) -> None:
        cutoff = now - self.window_seconds
        while log and log[0][0] < cutoff:
            log.pop(0)

    def _window_sum(self, log: list[tuple[float, int]], now: float) -> int:
        self._prune(log, now)
        return sum(tokens for _, tokens in log)

    async def acquire(self, *, estimated_input_tokens: int, estimated_output_tokens: int) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                input_used = self._window_sum(self._input_log, now)
                output_used = self._window_sum(self._output_log, now)
                input_ok = (input_used + estimated_input_tokens) <= self.input_tokens_per_minute
                output_ok = (output_used + estimated_output_tokens) <= self.output_tokens_per_minute
                if input_ok and output_ok:
                    self._input_log.append((now, estimated_input_tokens))
                    self._output_log.append((now, estimated_output_tokens))
                    return
                wait_seconds = 5.0
                logger.info(
                    "TokenBudgetManager: window at input=%s/%s output=%s/%s - waiting %.1fs",
                    input_used,
                    self.input_tokens_per_minute,
                    output_used,
                    self.output_tokens_per_minute,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)

    def record_actual(self, *, input_tokens: int, output_tokens: int) -> None:
        """Call after the API response to correct the estimate."""
        now = time.monotonic()
        self._input_log.append((now, input_tokens))
        self._output_log.append((now, output_tokens))
