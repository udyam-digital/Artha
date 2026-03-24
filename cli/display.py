from __future__ import annotations

from pathlib import Path
from typing import Any

from cli.format import _thesis_text, _verdict_to_action_text, format_rupees
from kite.runtime import KiteSyncResult
from models import PortfolioReport, PortfolioSnapshot, RebalancingAction, ResearchDigest, StockVerdict
from observability.usage import format_usage_summary
from rebalance import PASSIVE_INSTRUMENTS
from reliability import FullRunFailed


def _render_verdict_rows(verdicts: list[StockVerdict]) -> list[str]:
    header = "┌─────────────┬─────────────┬──────────┬────────┬──────────────────┐"
    title = "│ Stock       │ Verdict     │ Thesis   │ P&L%   │ Action           │"
    divider = "├─────────────┼─────────────┼──────────┼────────┼──────────────────┤"
    footer = "└─────────────┴─────────────┴──────────┴────────┴──────────────────┘"
    rows = [header, title, divider]
    for verdict in verdicts:
        pnl_text = f"{verdict.pnl_pct:+.0f}%"
        rows.append(
            "│ "
            f"{verdict.tradingsymbol:<11} │ "
            f"{verdict.verdict.value:<11} │ "
            f"{_thesis_text(verdict):<8} │ "
            f"{pnl_text:<6} │ "
            f"{_verdict_to_action_text(verdict):<16} │"
        )
    rows.append(footer)
    return rows


def print_report(report: PortfolioReport) -> None:
    timestamp = report.generated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    equity_count = len(report.verdicts)
    etf_count = len(
        [holding for holding in report.portfolio_snapshot.holdings if holding.tradingsymbol in PASSIVE_INSTRUMENTS]
    )
    print("╔══════════════════════════════════════╗")
    print("║  ARTHA PORTFOLIO REPORT              ║")
    print(f"║  {timestamp:<36}║")
    print("╚══════════════════════════════════════╝")
    print()
    print("PORTFOLIO SNAPSHOT")
    print(f"Total Value:    {format_rupees(report.portfolio_snapshot.total_value)}")
    print(f"Available Cash: {format_rupees(report.portfolio_snapshot.available_cash)}")
    print(f"Equity stocks:  {equity_count} | ETFs: {etf_count} (excluded from analysis)")
    print()
    print("ANALYST VERDICTS")
    if report.verdicts:
        for line in _render_verdict_rows(report.verdicts):
            print(line)
    else:
        print("No analyst verdicts in this run.")
    print()
    print("REBALANCING SUMMARY")
    print(f"Total to sell:  {format_rupees(report.total_sell_required)}")
    print(f"Total to buy:   {format_rupees(report.total_buy_required)}")
    print()
    print("PORTFOLIO SUMMARY")
    print(report.portfolio_summary)
    actionable = [verdict for verdict in report.verdicts if verdict.rebalance_action != "HOLD"]
    if actionable:
        print()
        print("WHAT AND WHY")
        for verdict in actionable:
            print(
                f"- {verdict.tradingsymbol}: {verdict.rebalance_action} "
                f"{format_rupees(verdict.rebalance_rupees)} because {verdict.rebalance_reasoning}"
            )
    if report.errors:
        print()
        print("ERRORS")
        for error in report.errors:
            print(f"- {error}")


def print_rebalance_report(report: PortfolioReport, actions: list[RebalancingAction]) -> None:
    timestamp = report.generated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    print("╔══════════════════════════════════════╗")
    print("║  ARTHA REBALANCE REPORT              ║")
    print(f"║  {timestamp:<36}║")
    print("╚══════════════════════════════════════╝")
    print()
    print("PORTFOLIO SNAPSHOT")
    print(f"Total Value:    {format_rupees(report.portfolio_snapshot.total_value)}")
    print(f"Available Cash: {format_rupees(report.portfolio_snapshot.available_cash)}")
    print()
    print("REBALANCING ACTIONS")
    if not actions:
        print("No actionable positions.")
    for action in actions:
        amount = format_rupees(action.rupee_amount) if action.action != "HOLD" else "—"
        print(
            f"{action.action:<4} {action.tradingsymbol:<12} {amount:<10} "
            f"({action.current_weight_pct:.1f}% → {action.target_weight_pct:.1f}%)  {action.urgency}"
        )
    print()
    print("PORTFOLIO SUMMARY")
    print(report.portfolio_summary)


def print_single_verdict(verdict: StockVerdict) -> None:
    print("STOCK VERDICT")
    print(f"Stock:                {verdict.tradingsymbol} ({verdict.company_name})")
    print(f"Verdict:              {verdict.verdict.value}")
    print(f"Confidence:           {verdict.confidence}")
    print(f"Thesis Intact:        {'Yes' if verdict.thesis_intact else 'No'}")
    print(f"Current Price:        {format_rupees(verdict.current_price)}")
    print(f"Buy Price:            {format_rupees(verdict.buy_price)}")
    print(f"P&L %:                {verdict.pnl_pct:+.1f}%")
    print(f"Action:               {_verdict_to_action_text(verdict)}")
    print(f"Bull Case:            {verdict.bull_case}")
    print(f"Bear Case:            {verdict.bear_case}")
    print(f"What To Watch:        {verdict.what_to_watch}")
    print(f"Rebalance Reasoning:  {verdict.rebalance_reasoning}")
    print(f"Sources:              {', '.join(verdict.data_sources) if verdict.data_sources else 'None'}")
    print(f"Duration:             {verdict.analysis_duration_seconds:.1f}s")
    if verdict.error:
        print(f"Error:                {verdict.error}")


def print_company_artifact(artifact: Any) -> None:
    print("ANALYST REPORT CARD")
    # Support both CompanyDataCard (new) and CompanyAnalysisArtifact (legacy)
    rc = artifact.analysis if hasattr(artifact, "analysis") else artifact.report_card
    yf_data = artifact.yfinance_data if hasattr(artifact, "yfinance_data") else {}
    print(f"Stock:                {artifact.ticker} ({rc.stock_snapshot.name})")
    print(f"Verdict:              {rc.final_verdict.verdict}")
    print(f"Confidence:           {rc.final_verdict.confidence}")
    print(f"Sector:               {rc.stock_snapshot.sector}")
    print(f"Current Price:        {format_rupees(rc.stock_snapshot.current_price)}")
    print(f"YFinance Fields:      {', '.join(sorted(yf_data)) if yf_data else 'None'}")
    print(f"Sources:              {len(rc.data_sources)}")
    print()
    print(artifact.model_dump_json(indent=2, by_alias=True))


def print_holdings(snapshot: PortfolioSnapshot) -> None:
    print(f"{'SYMBOL':<14}{'QTY':>8}{'LAST':>12}{'VALUE':>14}{'P&L':>14}{'P&L %':>10}")
    for holding in snapshot.holdings:
        print(
            f"{holding.tradingsymbol:<14}{holding.quantity:>8}"
            f"{holding.last_price:>12.2f}{holding.current_value:>14.2f}"
            f"{holding.pnl:>14.2f}{holding.pnl_pct:>10.2f}"
        )
    print()
    print(f"Total Value: {format_rupees(snapshot.total_value)}")
    print(f"Available Cash: {format_rupees(snapshot.available_cash)}")


def print_kite_login_result(auth_artifact: Path, auth_url: str | None, portfolio_artifact: Path) -> None:
    print("KITE MCP LOGIN")
    print(f"Auth artifact saved to: {auth_artifact}")
    if auth_url:
        print(f"Login URL: {auth_url}")
    print("Login completed successfully.")
    print(f"Portfolio snapshot saved to: {portfolio_artifact}")


def print_kite_sync_result(result: KiteSyncResult) -> None:
    print("KITE MCP SYNC")
    if result.profile:
        print(f"Profile fetched: {result.profile.get('user_name') or result.profile.get('user_id') or 'available'}")
    if result.auth_url:
        print(f"Login URL used: {result.auth_url}")
    print_holdings(result.portfolio_snapshot)
    print(f"Portfolio snapshot saved to: {result.portfolio_artifact}")
    print(f"MF snapshot saved to: {result.mf_artifact}")


def print_research_result(
    digest: ResearchDigest, digest_path: Path, holding_paths: list[Path], index_path: Path
) -> None:
    print("ARTHA DEEP RESEARCH")
    print(f"Equity reports: {len(digest.equity_reports)}")
    print(f"MF reports:     {len(digest.mf_reports)}")
    if digest.errors:
        print(f"Errors:         {len(digest.errors)}")
    print()
    print(digest.portfolio_digest)
    print()
    print(f"Combined digest saved to: {digest_path}")
    print(f"Research index saved to:  {index_path}")
    print(f"Holding reports saved:    {len(holding_paths)}")


def print_run_failure(exc: FullRunFailed, usage_summary: object) -> None:
    print("ARTHA RUN FAILED")
    print(f"Phase:                 {exc.phase}")
    if exc.ticker:
        print(f"Holding:               {exc.ticker}")
    print(f"Retries Used:          {exc.retries_used}")
    print(f"Error:                 {exc.message}")
    if exc.partial_artifact_path:
        print(f"Partial Artifact Path: {exc.partial_artifact_path}")
    if exc.error_log_path:
        print(f"Error Log Saved To:    {exc.error_log_path}")
    print()
    print(format_usage_summary(usage_summary))
    print(f"LLM usage log saved to: {usage_summary.usage_path}")
