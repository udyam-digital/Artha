from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from analysis.analyst import analyse_stock
from config import Settings
from tests.analyst_support import (
    PASSING_FACTUAL_SCORES,
    PASSING_QUALITY_SCORES,
    FakeAnthropicClient,
    make_final_response,
    make_holding,
    make_settings,
    mock_judges_and_providers,  # noqa: F401
)

pytestmark = pytest.mark.anyio


async def test_analyse_stock_retries_on_low_judge_score(tmp_path: Path) -> None:
    low_quality = {**PASSING_QUALITY_SCORES, "overall": 20, "key_issues": ["Stale data"]}
    low_factual = {**PASSING_FACTUAL_SCORES, "overall": 15, "red_flags": ["No real sources"]}
    client = FakeAnthropicClient(
        [
            make_final_response("KPITTECH"),
            make_final_response("KPITTECH"),
            make_final_response("KPITTECH"),
            make_final_response("KPITTECH"),
        ]
    )
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",  # pragma: allowlist secret
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
        MODEL="claude-sonnet-4-6",
        ANALYST_MODEL="claude-haiku-4-5",
        JUDGE_RETRY_THRESHOLD=45,
        JUDGE_MAX_RETRIES=1,
    )
    call_count = 0

    async def quality_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return low_quality if call_count == 1 else PASSING_QUALITY_SCORES

    async def factual_side_effect(*args, **kwargs):
        return low_factual if call_count <= 1 else PASSING_FACTUAL_SCORES

    with (
        patch("analysis.analyst_runtime.judge_report_card", side_effect=quality_side_effect),
        patch("analysis.analyst_runtime.judge_factual_grounding", side_effect=factual_side_effect),
    ):
        verdict = await analyse_stock(
            holding=make_holding(),
            portfolio_total_value=10_000.0,
            price_context={},
            skills_content="system",
            client=client,
            config=settings,
        )  # type: ignore[arg-type]

    assert verdict.verdict == "BUY"
    assert verdict.error is None
    assert len(client.calls) == 4
    assert (tmp_path / "kite" / "companies" / "KPITTECH_judge.json").exists()


async def test_analyse_stock_persists_judge_scores(tmp_path: Path) -> None:
    client = FakeAnthropicClient([make_final_response("KPITTECH")])
    await analyse_stock(
        holding=make_holding(),
        portfolio_total_value=10_000.0,
        price_context={},
        skills_content="system",
        client=client,
        config=make_settings(tmp_path),
    )  # type: ignore[arg-type]
    judge_path = tmp_path / "kite" / "companies" / "KPITTECH_judge.json"
    assert judge_path.exists()
    import json

    scores = json.loads(judge_path.read_text())
    assert scores["ticker"] == "KPITTECH"
    assert scores["quality_scores"] is not None
    assert scores["factual_scores"] is not None
    assert scores["combined_overall"] > 0
    assert scores["passed"] is True
