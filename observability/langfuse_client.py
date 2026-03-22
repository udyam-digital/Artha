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
) -> Langfuse | None:
    if not public_key or not secret_key:
        return None
    try:
        from langfuse import Langfuse

        lf = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info("Langfuse initialised (host=%s)", host)
        return lf
    except ImportError:
        logger.warning("langfuse not installed; tracing disabled")
        return None


def get_langfuse(settings: Settings) -> Langfuse | None:
    return _build_langfuse(
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
        settings.langfuse_base_url,
    )


def init_langfuse(settings: Settings) -> None:
    """
    Eagerly initialise Langfuse so the OpenTelemetry provider is active
    before any @observe-decorated functions run. Safe to call multiple times.
    """
    get_langfuse(settings)


def score_active_trace(
    lf: Langfuse,
    judge_result: dict[str, Any],
    ticker: str,
    factual_result: dict[str, Any] | None = None,
) -> None:
    """
    Post all judge dimension scores to the *currently active* Langfuse trace.
    Must be called from inside an @observe-decorated function.
    """
    try:
        # Quality judge dimensions
        dimensions: dict[str, float] = {
            "judge-recency": judge_result.get("recency", 0),
            "judge-risk": judge_result.get("risk_completeness", 0),
            "judge-valuation": judge_result.get("valuation_accuracy", 0),
            "judge-verdict-logic": judge_result.get("verdict_logic", 0),
        }
        # Factual judge dimensions
        if factual_result:
            dimensions["judge-source-grounding"] = factual_result.get("source_grounding", 0)
            dimensions["judge-hallucination-risk"] = factual_result.get("hallucination_risk", 0)
            dimensions["judge-data-consistency"] = factual_result.get("data_consistency", 0)

        for name, raw_score in dimensions.items():
            lf.score_current_trace(
                name=name,
                value=round(raw_score / 100, 4),  # normalise 0-100 → 0.0-1.0
            )
        lf.score_current_trace(
            name="judge-overall",
            value=round(judge_result.get("overall", 0) / 100, 4),
            comment=judge_result.get("one_line_summary", ""),
        )
        if factual_result:
            lf.score_current_trace(
                name="judge-factual-overall",
                value=round(factual_result.get("overall", 0) / 100, 4),
                comment=factual_result.get("one_line_summary", ""),
            )
    except Exception as exc:
        logger.warning("[%s] Langfuse in-context scoring failed (non-fatal): %s", ticker, exc)
