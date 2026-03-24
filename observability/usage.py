"""Compatibility wrapper around split observability modules."""

from __future__ import annotations

import observability.tracking as _tracking
from observability.pricing import _MODEL_PRICING, ModelPricing  # noqa: F401
from observability.reports import format_run_summary, format_usage_summary, load_recent_run_summaries  # noqa: F401
from observability.token_counting import (  # noqa: F401
    count_input_tokens_exact,
    estimate_input_tokens,
    log_estimated_input_tokens,
)
from observability.tracking import (  # noqa: F401  # noqa: F401
    UsageRunSummary,
    _append_jsonl,
    emit_span,
    get_current_usage_run,
    get_langfuse,
    start_span,
)


def _sync_tracking_hooks() -> None:
    _tracking._append_jsonl = _append_jsonl
    _tracking.emit_span = emit_span
    _tracking.get_langfuse = get_langfuse
    _tracking.start_span = start_span


def usage_run(*, settings, command):
    _sync_tracking_hooks()
    return _tracking.usage_run(settings=settings, command=command)


def record_run_error(*, settings, phase, error, retries_used, ticker=None, partial_artifact_path=None):
    _sync_tracking_hooks()
    return _tracking.record_run_error(
        settings=settings,
        phase=phase,
        error=error,
        retries_used=retries_used,
        ticker=ticker,
        partial_artifact_path=partial_artifact_path,
    )


def record_anthropic_usage(*, settings, label, model, response, metadata=None):
    _sync_tracking_hooks()
    return _tracking.record_anthropic_usage(
        settings=settings,
        label=label,
        model=model,
        response=response,
        metadata=metadata,
    )
