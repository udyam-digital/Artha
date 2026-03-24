from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from config import Settings
from models import Holding, MFHolding, MFSnapshot, PortfolioReport, PortfolioSnapshot, StockVerdict, Verdict


def make_settings(tmp_path: Path) -> Settings:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    return Settings(
        ANTHROPIC_API_KEY="test-key",  # pragma: allowlist secret
        REPORTS_DIR=str(reports_dir),
        LLM_USAGE_DIR=str(reports_dir / "usage"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


def make_report() -> PortfolioReport:
    snapshot = PortfolioSnapshot(
        fetched_at=datetime(2026, 3, 19, tzinfo=UTC),
        total_value=125000.0,
        available_cash=5000.0,
        holdings=[
            Holding(
                tradingsymbol="KPITTECH",
                exchange="NSE",
                quantity=10,
                average_price=1000.0,
                last_price=1100.0,
                current_value=11000.0,
                current_weight_pct=8.8,
                target_weight_pct=10.0,
                pnl=1000.0,
                pnl_pct=10.0,
                instrument_token=12345,
            )
        ],
    )
    return PortfolioReport(
        generated_at=datetime(2026, 3, 19, 12, 0, tzinfo=UTC),
        portfolio_snapshot=snapshot,
        verdicts=[
            StockVerdict(
                tradingsymbol="KPITTECH",
                company_name="KPIT Technologies",
                verdict=Verdict.BUY,
                confidence="HIGH",
                current_price=1100.0,
                buy_price=1000.0,
                pnl_pct=10.0,
                thesis_intact=True,
                bull_case="Bull case",
                bear_case="Bear case",
                what_to_watch="Watch this",
                red_flags=["Flag 1"],
                rebalance_action="BUY",
                rebalance_rupees=2000.0,
                rebalance_reasoning="Reason",
                data_sources=["https://example.com"],
                analysis_duration_seconds=12.5,
                error="partial data",
            )
        ],
        portfolio_summary="Summary",
        total_buy_required=2000.0,
        total_sell_required=0.0,
        errors=["one issue"],
    )


def write_report(settings: Settings, report: PortfolioReport, stem: str) -> Path:
    path = settings.reports_dir / f"{stem}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def parse_sse_stream(raw: bytes) -> list[dict]:
    events = []
    current: dict = {}
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("event:"):
            current["event"] = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line.removeprefix("data:").strip())
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def build_cached_holdings(settings: Settings) -> None:
    snapshot = PortfolioSnapshot(
        fetched_at=datetime(2026, 3, 19, tzinfo=UTC),
        total_value=125000.0,
        available_cash=5000.0,
        holdings=[
            Holding(
                tradingsymbol="KPITTECH",
                exchange="NSE",
                quantity=10,
                average_price=1000.0,
                last_price=1100.0,
                current_value=11000.0,
                current_weight_pct=8.8,
                target_weight_pct=10.0,
                pnl=1000.0,
                pnl_pct=10.0,
                instrument_token=12345,
            )
        ],
    )
    mf_snapshot = MFSnapshot(
        fetched_at=datetime(2026, 3, 19, tzinfo=UTC),
        total_value=1000.0,
        holdings=[
            MFHolding(
                tradingsymbol="MF1",
                fund="Fund 1",
                folio="folio",
                quantity=1.0,
                average_price=100.0,
                last_price=110.0,
                current_value=110.0,
                pnl=10.0,
                pnl_pct=10.0,
                scheme_type="Equity",
                plan="Direct",
            )
        ],
    )
    portfolio_path = settings.kite_data_dir / "portfolio" / "latest_snapshot.json"
    portfolio_path.parent.mkdir(parents=True, exist_ok=True)
    portfolio_path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    mf_path = settings.kite_data_dir / "mf" / "latest_snapshot.json"
    mf_path.parent.mkdir(parents=True, exist_ok=True)
    mf_path.write_text(mf_snapshot.model_dump_json(), encoding="utf-8")
