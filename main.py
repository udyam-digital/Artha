from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import AsyncAnthropic

from config import configure_logging, get_settings
from kite_runtime import KiteSyncResult, sync_kite_data
from models import Holding, PortfolioReport, PortfolioSnapshot, ResearchDigest, RebalancingAction, StockVerdict
from orchestrator import run_full_analysis, run_single_company_analysis
from rebalance import PASSIVE_INSTRUMENTS, calculate_rebalancing_actions
from research import DeepResearchOrchestrator
from snapshot_store import load_latest_portfolio_snapshot
from tools import ToolExecutionError
from usage_tracking import format_usage_summary, usage_run

logger = logging.getLogger(__name__)


def format_rupees(amount: float) -> str:
    return f"\u20b9{amount:,.0f}"


def _verdict_to_action_text(verdict: StockVerdict) -> str:
    if verdict.rebalance_action == "HOLD":
        return "HOLD —"
    return f"{verdict.rebalance_action} {format_rupees(verdict.rebalance_rupees)}"


def _thesis_text(verdict: StockVerdict) -> str:
    return "✓ Intact" if verdict.thesis_intact else "✗ Weak"


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


def build_rebalance_only_report(snapshot: PortfolioSnapshot) -> tuple[PortfolioReport, list[RebalancingAction]]:
    actions = calculate_rebalancing_actions(
        holdings=snapshot.holdings,
        total_value=snapshot.total_value,
        available_cash=snapshot.available_cash,
    )
    summary = (
        "This is a rebalance-only run using live holdings and current market values. "
        "Fundamental analysis was skipped, so actions are based only on drift versus target weights. "
        "Review tax context and thesis quality before acting on any sell recommendation."
    )
    report = PortfolioReport(
        generated_at=datetime.now(timezone.utc),
        portfolio_snapshot=snapshot,
        verdicts=[],
        portfolio_summary=summary,
        total_buy_required=sum(action.rupee_amount for action in actions if action.action == "BUY"),
        total_sell_required=sum(action.rupee_amount for action in actions if action.action == "SELL"),
        errors=[],
    )
    return report, actions


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


def build_standalone_holding(symbol: str, exchange: str = "NSE") -> Holding:
    return Holding(
        tradingsymbol=symbol.upper(),
        exchange=exchange.upper(),
        quantity=0,
        average_price=0.0,
        last_price=0.0,
        current_value=0.0,
        current_weight_pct=0.0,
        target_weight_pct=0.0,
        pnl=0.0,
        pnl_pct=0.0,
        instrument_token=0,
    )


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


def print_research_result(digest: ResearchDigest, digest_path: Path, holding_paths: list[Path], index_path: Path) -> None:
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


def save_report(report: PortfolioReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    filename = report.generated_at.strftime("%Y%m%d_%H%M%S_artha_report.json")
    output_path = reports_dir / filename
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return output_path


async def handle_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.rebalance_only and args.ticker:
        raise ValueError("--ticker cannot be combined with --rebalance-only")

    if args.rebalance_only:
        sync_result = await sync_kite_data(settings=settings)
        snapshot = sync_result.portfolio_snapshot
        report, actions = build_rebalance_only_report(snapshot)
        output_path = save_report(report, settings.reports_dir)
        print_rebalance_report(report, actions)
        print()
        print(f"JSON report saved to: {output_path}")
        return 0

    if args.ticker:
        with usage_run(settings=settings, command=f"run --ticker {args.ticker.upper()}") as usage_summary:
            report = await run_single_company_analysis(
                settings=settings,
                ticker=args.ticker,
                exchange=getattr(args, "exchange", "NSE"),
            )
        output_path = save_report(report, settings.reports_dir)
        print_report(report)
        print()
        print(format_usage_summary(usage_summary))
        print(f"LLM usage log saved to: {usage_summary.usage_path}")
        print()
        print(f"JSON report saved to: {output_path}")
        return 0

    started = time.perf_counter()

    def progress_callback(completed: int, total: int, verdict: StockVerdict) -> None:
        print(
            f"[{completed}/{total}] {verdict.tradingsymbol:<10} "
            f"✓ {verdict.verdict.value:<9} ({verdict.analysis_duration_seconds:.1f}s)"
        )

    with usage_run(settings=settings, command="run") as usage_summary:
        report = await run_full_analysis(settings, progress_callback=progress_callback)

    output_path = save_report(report, settings.reports_dir)
    print()
    print_report(report)
    print()
    print(
        f"Completed in {time.perf_counter() - started:.1f}s | "
        f"{len(report.verdicts)} analysts | {len(report.errors)} errors"
    )
    print()
    print(format_usage_summary(usage_summary))
    print(f"LLM usage log saved to: {usage_summary.usage_path}")
    print()
    print(f"JSON report saved to: {output_path}")
    return 0


async def handle_rebalance() -> int:
    settings = get_settings()
    snapshot = load_latest_portfolio_snapshot(settings)
    report, actions = build_rebalance_only_report(snapshot)
    output_path = save_report(report, settings.reports_dir)
    print_rebalance_report(report, actions)
    print()
    print(f"JSON report saved to: {output_path}")
    return 0


async def handle_holdings() -> int:
    settings = get_settings()
    sync_result = await sync_kite_data(settings=settings)
    snapshot = sync_result.portfolio_snapshot
    print_holdings(snapshot)
    return 0


async def handle_kite_sync() -> int:
    result = await sync_kite_data(settings=get_settings())
    print_kite_sync_result(result)
    return 0


async def handle_kite_login() -> int:
    result = await sync_kite_data(settings=get_settings())
    print_kite_login_result(result.auth_artifact or result.portfolio_artifact, result.auth_url, result.portfolio_artifact)
    print(f"MF snapshot saved to: {result.mf_artifact}")
    return 0


async def handle_research() -> int:
    settings = get_settings()
    orchestrator = DeepResearchOrchestrator(settings=settings)
    with usage_run(settings=settings, command="research") as usage_summary:
        digest, digest_path, holding_paths, index_path = await orchestrator.research_latest_snapshots()
    print_research_result(digest, digest_path, holding_paths, index_path)
    print()
    print(format_usage_summary(usage_summary))
    print(f"LLM usage log saved to: {usage_summary.usage_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Artha portfolio research and rebalancing agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Full portfolio analysis and rebalancing report")
    run_parser.add_argument("--ticker", help="Run a single-stock deep dive only")
    run_parser.add_argument("--exchange", default="NSE", help="Exchange for standalone analyst mode, default NSE")
    run_parser.add_argument(
        "--rebalance-only",
        action="store_true",
        help="Skip fundamental analysis and compute only rebalancing actions after a fresh sync",
    )

    subparsers.add_parser("holdings", help="Print the current holdings table without an LLM call")
    subparsers.add_parser("kite-login", help="Start Kite login, wait for completion on the same MCP session, and save a snapshot")
    subparsers.add_parser("kite-sync", help="Fetch fresh equity and MF snapshots from Kite MCP and save them locally")
    subparsers.add_parser("rebalance", help="Generate a rebalancing report from the latest saved local equity snapshot")
    subparsers.add_parser("research", help="Run deep web research on the latest saved equity and MF snapshots")
    return parser


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        if args.command == "run":
            return await handle_run(args)
        if args.command == "holdings":
            return await handle_holdings()
        if args.command == "kite-login":
            return await handle_kite_login()
        if args.command == "kite-sync":
            return await handle_kite_sync()
        if args.command == "rebalance":
            return await handle_rebalance()
        if args.command == "research":
            return await handle_research()
        parser.error("Unknown command")
        return 2
    except ToolExecutionError as exc:
        logger.error(str(exc))
        return 1
    except Exception:
        logger.exception("Artha failed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(async_main()))
