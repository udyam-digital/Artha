import asyncio
from pathlib import Path
from types import SimpleNamespace

from application.research import DeepResearchOrchestrator, ResearchExecutionError
from config import Settings
from models import Holding, MFHolding, MFSnapshot, PortfolioSnapshot


class FakeAnthropicClient:
    def __init__(self) -> None:
        self.calls = []

    async def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        if "mutual fund" in prompt:
            text = (
                '<mf_research>{"identifier":"AXISMIDCAP","title":"Axis Midcap Fund",'
                '"data_freshness":"Latest factsheet reviewed","sources":["https://example.com/mf"],'
                '"fund_house":"Axis","category":"Mid Cap","mandate":"Mid-cap equity",'
                '"portfolio_style":"Growth","expense_ratio_note":"Competitive direct-plan expense ratio",'
                '"aum_note":"Healthy AUM","overlap_risk":"Moderate overlap with diversified equity funds",'
                '"recent_commentary":"Positioning remains growth-oriented","risks":["Mid-cap volatility"],'
                '"confidence_summary":"Enough current data collected."}</mf_research>'
            )
        elif "Input JSON" in prompt:
            text = "Portfolio digest"
        else:
            text = (
                '<equity_research>{"identifier":"HDFCBANK","title":"HDFC Bank",'
                '"data_freshness":"Q3 FY26 results available","sources":["https://example.com/equity"],'
                '"bull_case":"Strong franchise","bear_case":"Margin pressure",'
                '"what_to_watch":"Loan growth","red_flags":[],"confidence_summary":"Enough current data collected."}'
                "</equity_research>"
            )
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    @property
    def messages(self):
        return SimpleNamespace(create=self.messages_create)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        MODEL="claude-sonnet-4-6",
        ANALYST_MODEL="claude-haiku-4-5",
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
        ANALYST_PARALLELISM=1,
    )


def test_research_orchestrator_saves_equity_and_mf_reports(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = FakeAnthropicClient()
    orchestrator = DeepResearchOrchestrator(settings=settings, client=client)  # type: ignore[arg-type]
    portfolio_snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=1000.0,
        available_cash=0.0,
        holdings=[
            Holding(
                tradingsymbol="HDFCBANK",
                exchange="NSE",
                quantity=1,
                average_price=100.0,
                last_price=120.0,
                current_value=120.0,
                current_weight_pct=12.0,
                target_weight_pct=10.0,
                pnl=20.0,
                pnl_pct=20.0,
                instrument_token=1,
            )
        ],
    )
    mf_snapshot = MFSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=500.0,
        holdings=[
            MFHolding(
                tradingsymbol="AXISMIDCAP",
                fund="Axis Midcap Fund",
                folio="123",
                quantity=10.0,
                average_price=40.0,
                last_price=50.0,
                current_value=500.0,
                pnl=100.0,
                pnl_pct=25.0,
                scheme_type="Equity",
                plan="Direct",
            )
        ],
    )
    digest, digest_path, holding_paths, index_path = asyncio.run(
        orchestrator.research_snapshots(
            portfolio_snapshot,
            mf_snapshot,
        )
    )
    assert len(digest.equity_reports) == 1
    assert len(digest.mf_reports) == 1
    assert digest_path.exists()
    assert index_path.exists()
    assert len(holding_paths) == 2
    assert client.calls[-1]["max_tokens"] == settings.summary_max_tokens


def test_research_loop_fails_fast_on_unexpected_stop_reason(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    class UnexpectedStopClient:
        @property
        def messages(self):
            async def create(**kwargs):
                del kwargs
                return SimpleNamespace(stop_reason="refusal", content=[])

            return SimpleNamespace(create=create)

    orchestrator = DeepResearchOrchestrator(settings=settings, client=UnexpectedStopClient())  # type: ignore[arg-type]

    async def run_test() -> None:
        try:
            await orchestrator._run_tool_loop(  # noqa: SLF001
                system="system",
                user_prompt="prompt",
                label="research_equity:HDFCBANK",
                metadata={"phase": "research_equity"},
            )
        except ResearchExecutionError as exc:
            assert "Unexpected stop_reason" in str(exc)
        else:
            raise AssertionError("Expected ResearchExecutionError")

    asyncio.run(run_test())


def test_research_orchestrator_loads_skills_independent_of_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    orchestrator = DeepResearchOrchestrator(settings=make_settings(tmp_path), client=FakeAnthropicClient())  # type: ignore[arg-type]
    assert "You are Artha" in orchestrator.equity_framework
    assert "portfolio" in orchestrator.portfolio_rules.lower()


def test_research_snapshots_respects_parallelism_limit(tmp_path: Path) -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        MODEL="claude-sonnet-4-6",
        ANALYST_MODEL="claude-haiku-4-5",
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
        ANALYST_PARALLELISM=2,
        ANALYST_MIN_START_INTERVAL_SECONDS=0,
    )
    orchestrator = DeepResearchOrchestrator(settings=settings, client=FakeAnthropicClient())  # type: ignore[arg-type]
    portfolio_snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=1000.0,
        available_cash=0.0,
        holdings=[
            Holding(
                tradingsymbol="HDFCBANK",
                exchange="NSE",
                quantity=1,
                average_price=100.0,
                last_price=120.0,
                current_value=120.0,
                current_weight_pct=12.0,
                target_weight_pct=10.0,
                pnl=20.0,
                pnl_pct=20.0,
                instrument_token=1,
            ),
            Holding(
                tradingsymbol="KPITTECH",
                exchange="NSE",
                quantity=1,
                average_price=100.0,
                last_price=120.0,
                current_value=120.0,
                current_weight_pct=12.0,
                target_weight_pct=10.0,
                pnl=20.0,
                pnl_pct=20.0,
                instrument_token=2,
            ),
        ],
    )
    mf_snapshot = None
    state = {"active": 0, "max_active": 0}

    async def fake_research_equity_holding(holding):
        del holding
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0)
        state["active"] -= 1
        return orchestrator._extract_tagged_json(  # noqa: SLF001
            '<equity_research>{"identifier":"TMP","title":"Tmp","data_freshness":"Now","sources":[],"bull_case":"","bear_case":"","what_to_watch":"","red_flags":[],"confidence_summary":""}</equity_research>',
            "equity_research",
            "TMP",
        )

    async def fake_build_digest_text(equity_reports, mf_reports, errors):
        del equity_reports, mf_reports, errors
        return "digest"

    async def wrapped_fake_research(holding):
        from models import EquityResearchArtifact

        payload = await fake_research_equity_holding(holding)
        return EquityResearchArtifact(
            generated_at="2026-03-18T10:00:00Z",
            identifier=payload["identifier"],
            title=payload["title"],
            data_freshness=payload["data_freshness"],
            sources=payload["sources"],
            bull_case=payload["bull_case"],
            bear_case=payload["bear_case"],
            what_to_watch=payload["what_to_watch"],
            red_flags=payload["red_flags"],
            confidence_summary=payload["confidence_summary"],
        )

    orchestrator._research_equity_holding = wrapped_fake_research  # type: ignore[method-assign]
    orchestrator._build_digest_text = fake_build_digest_text  # type: ignore[method-assign]

    asyncio.run(orchestrator.research_snapshots(portfolio_snapshot, mf_snapshot))
    assert state["max_active"] <= settings.analyst_parallelism


def test_research_snapshots_uses_unique_payload_keys_for_duplicate_identifiers(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    orchestrator = DeepResearchOrchestrator(settings=settings, client=FakeAnthropicClient())  # type: ignore[arg-type]
    portfolio_snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=1000.0,
        available_cash=0.0,
        holdings=[],
    )
    mf_snapshot = None
    captured_payloads: dict[str, dict] = {}

    async def fake_run_research_jobs(_jobs):
        from models import EquityResearchArtifact, MFResearchArtifact

        return [
            (
                "equity",
                EquityResearchArtifact(
                    generated_at="2026-03-18T10:00:00Z",
                    identifier="DUPLICATE",
                    title="One",
                    data_freshness="Now",
                    sources=[],
                    bull_case="",
                    bear_case="",
                    what_to_watch="",
                    red_flags=[],
                    confidence_summary="",
                ),
            ),
            (
                "mf",
                MFResearchArtifact(
                    generated_at="2026-03-18T10:00:00Z",
                    identifier="DUPLICATE",
                    title="Two",
                    data_freshness="Now",
                    sources=[],
                    fund_house="",
                    category="",
                    mandate="",
                    portfolio_style="",
                    expense_ratio_note="",
                    aum_note="",
                    overlap_risk="",
                    recent_commentary="",
                    risks=[],
                    confidence_summary="",
                ),
            ),
        ]

    async def fake_build_digest_text(equity_reports, mf_reports, errors):
        del equity_reports, mf_reports, errors
        return "digest"

    def fake_save_research_digest(digest, per_holding_payloads, settings):
        del digest, settings
        captured_payloads.update(per_holding_payloads)
        return tmp_path / "digest.json", [], tmp_path / "index.json"

    orchestrator._run_research_jobs = fake_run_research_jobs  # type: ignore[method-assign]
    orchestrator._build_digest_text = fake_build_digest_text  # type: ignore[method-assign]

    from application import research as research_module

    original_save = research_module.save_research_digest
    research_module.save_research_digest = fake_save_research_digest  # type: ignore[assignment]
    try:
        asyncio.run(orchestrator.research_snapshots(portfolio_snapshot, mf_snapshot))
    finally:
        research_module.save_research_digest = original_save  # type: ignore[assignment]

    assert sorted(captured_payloads) == ["EQUITY_DUPLICATE", "MF_DUPLICATE"]


def test_run_tool_loop_uses_analyst_model_and_tokens(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = FakeAnthropicClient()
    orchestrator = DeepResearchOrchestrator(settings=settings, client=client)  # type: ignore[arg-type]

    asyncio.run(
        orchestrator._run_tool_loop(  # noqa: SLF001
            system="system",
            user_prompt="prompt",
            label="research_equity:HDFCBANK",
            metadata={"phase": "research_equity"},
        )
    )

    assert client.calls[0]["model"] == settings.analyst_model
    assert client.calls[0]["max_tokens"] == settings.analyst_max_tokens
