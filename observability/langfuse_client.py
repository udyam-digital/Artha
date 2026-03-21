from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from config import Settings

if TYPE_CHECKING:
    from langfuse import Langfuse


logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _build_langfuse(
    public_key: str,
    secret_key: str,
    host: str,
) -> "Langfuse | None":
    if not public_key or not secret_key:
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
    except ImportError:
        logger.warning("langfuse not installed; tracing disabled")
        return None


def get_langfuse(settings: Settings) -> "Langfuse | None":
    return _build_langfuse(
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
        settings.langfuse_base_url,
    )


def score_analyst_trace(
    *,
    settings: Settings,
    ticker: str,
    trace_id: str | None,
    eval_result: dict[str, Any],
) -> None:
    """
    Post LLM-as-Judge scores to Langfuse as scores on the analyst trace.
    Silently no-ops if Langfuse is not configured or not installed.
    """
    lf = get_langfuse(settings)
    if lf is None:
        return
    try:
        overall = eval_result.get("overall", 0)
        comment_parts: list[str] = []
        for key in ("growth", "risk", "sources", "verdict_consistency"):
            section = eval_result.get(key, {})
            score = section.get("score", 0)
            failures = section.get("failures", [])
            comment_parts.append(f"{key}={score}")
            if failures:
                comment_parts.append(f"  issues: {'; '.join(failures)}")
        comment = "\n".join(comment_parts)

        score_kwargs: dict[str, Any] = {
            "name": "llm-judge-overall",
            "value": overall,
            "comment": comment,
        }
        if trace_id:
            score_kwargs["trace_id"] = trace_id

        lf.score(**score_kwargs)
    except Exception as exc:
        logger.warning("[%s] Langfuse score post failed (non-fatal): %s", ticker, exc)
