from pathlib import Path

from application.agent import ArthaAgent
from config import Settings
from models import PortfolioSnapshot


def make_settings(tmp_path: Path) -> Settings:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        MODEL="claude-sonnet-4-6",
        ANALYST_MODEL="claude-haiku-4-5",
        COMPANY_ANALYSIS_MAX_AGE_DAYS=7,
        REPORTS_DIR=str(reports_dir),
        KITE_MCP_URL="https://mcp.kite.trade/mcp",
    )


def test_tool_definitions_include_tavily_search(tmp_path: Path) -> None:
    agent = ArthaAgent(settings=make_settings(tmp_path), client=object())  # type: ignore[arg-type]
    tool_names = [tool["name"] for tool in agent.tools]
    assert tool_names == [
        "kite_get_portfolio",
        "kite_get_price_history",
        "tavily_search",
    ]
    assert "type" not in next(tool for tool in agent.tools if tool["name"] == "tavily_search")


def test_full_run_prompt_requires_deep_research(tmp_path: Path) -> None:
    agent = ArthaAgent(settings=make_settings(tmp_path), client=object())  # type: ignore[arg-type]
    prompt = agent._build_user_prompt()
    assert "For every non-passive equity holding" in prompt
    assert "use tavily_search up to 3 times per holding" in prompt.lower()


def test_parse_final_output_falls_back_without_tags(tmp_path: Path) -> None:
    agent = ArthaAgent(settings=make_settings(tmp_path), client=object())  # type: ignore[arg-type]
    report = agent._parse_final_output("not valid output", snapshot=None, errors=[])
    assert report.portfolio_summary == "not valid output"
    assert report.errors


def test_fallback_report_uses_verdicts_field(tmp_path: Path) -> None:
    agent = ArthaAgent(settings=make_settings(tmp_path), client=object())  # type: ignore[arg-type]
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=1000.0,
        available_cash=0.0,
        holdings=[],
    )
    report = agent._fallback_report("summary", snapshot=snapshot, errors=[])
    assert report.verdicts == []
