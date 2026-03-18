from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent import ArthaAgent
from config import configure_logging, get_settings
from models import PortfolioReport, PortfolioSnapshot
from rebalance import calculate_rebalancing_actions
from tools import (
    KiteMCPClient,
    ToolExecutionError,
    kite_get_portfolio,
    kite_get_profile,
    kite_login,
    load_kite_server_definition,
    profile_requires_login,
    save_portfolio_snapshot,
    wait_for_kite_login,
)

logger = logging.getLogger(__name__)


def format_rupees(amount: float) -> str:
    return f"\u20b9{amount:,.0f}"


def build_rebalance_only_report(snapshot: PortfolioSnapshot) -> PortfolioReport:
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
    return PortfolioReport(
        generated_at=datetime.now(timezone.utc),
        portfolio_snapshot=snapshot,
        analyses=[],
        rebalancing_actions=actions,
        portfolio_summary=summary,
        total_buy_required=sum(action.rupee_amount for action in actions if action.action == "BUY"),
        total_sell_required=sum(action.rupee_amount for action in actions if action.action == "SELL"),
        errors=[],
    )


def print_report(report: PortfolioReport) -> None:
    timestamp = report.generated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    holdings_count = len(report.portfolio_snapshot.holdings)
    print("╔══════════════════════════════╗")
    print("║  ARTHA PORTFOLIO REPORT      ║")
    print(f"║  {timestamp:<26}║")
    print("╚══════════════════════════════╝")
    print()
    print("PORTFOLIO SNAPSHOT")
    print(f"Total Value:    {format_rupees(report.portfolio_snapshot.total_value)}")
    print(f"Available Cash: {format_rupees(report.portfolio_snapshot.available_cash)}")
    print(f"Holdings:       {holdings_count} stocks")
    print()
    print("REBALANCING ACTIONS")
    if report.rebalancing_actions:
        for action in report.rebalancing_actions:
            if action.action == "SELL":
                prefix = "🔴 SELL"
                amount = format_rupees(action.rupee_amount)
            elif action.action == "BUY":
                prefix = "🟢 BUY "
                amount = format_rupees(action.rupee_amount)
            else:
                prefix = "⚪ HOLD"
                amount = "—"
            print(
                f"{prefix:<8} {action.tradingsymbol:<12} {amount:<10} "
                f"({action.current_weight_pct:.1f}% → {action.target_weight_pct:.1f}%)  {action.urgency}"
            )
    else:
        print("No actionable positions.")
    print()
    print("ANALYSIS HIGHLIGHTS")
    if report.analyses:
        for analysis in report.analyses:
            flag_text = ", ".join(analysis.red_flags) if analysis.red_flags else "None identified"
            print(f"{analysis.tradingsymbol}: {analysis.bull_case} | Risk: {flag_text}")
    else:
        print("No fundamental analysis in this run.")
    print()
    print("PORTFOLIO SUMMARY")
    print(report.portfolio_summary)
    if report.errors:
        print()
        print("ERRORS")
        for error in report.errors:
            print(f"- {error}")


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


def print_kite_sync_result(profile: dict[str, object], snapshot: PortfolioSnapshot, artifact_path: Path) -> None:
    print("KITE MCP SYNC")
    if profile:
        print(f"Profile fetched: {profile.get('user_name') or profile.get('user_id') or 'available'}")
    print_holdings(snapshot)
    print(f"Portfolio snapshot saved to: {artifact_path}")


def save_report(report: PortfolioReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    filename = report.generated_at.strftime("%Y%m%d_%H%M%S_artha_report.json")
    output_path = reports_dir / filename
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return output_path


def build_kite_client(settings) -> KiteMCPClient:
    return KiteMCPClient(
        load_kite_server_definition(settings),
        timeout_seconds=settings.kite_mcp_timeout_seconds,
    )


async def handle_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.rebalance_only and args.ticker:
        raise ValueError("--ticker cannot be combined with --rebalance-only")

    if args.rebalance_only:
        async with build_kite_client(settings) as kite_client:
            snapshot = await kite_get_portfolio(kite_client, settings=settings)
        report = build_rebalance_only_report(snapshot)
    else:
        agent = ArthaAgent(settings=settings)
        report = await agent.run(ticker=args.ticker)

    output_path = save_report(report, settings.reports_dir)
    print_report(report)
    print()
    print(f"JSON report saved to: {output_path}")
    return 0


async def handle_holdings() -> int:
    settings = get_settings()
    async with build_kite_client(settings) as kite_client:
        snapshot = await kite_get_portfolio(kite_client, settings=settings)
    print_holdings(snapshot)
    return 0


async def handle_kite_sync() -> int:
    settings = get_settings()
    async with build_kite_client(settings) as kite_client:
        profile = await kite_get_profile(kite_client)
        snapshot = await kite_get_portfolio(kite_client, settings=settings)
    artifact_path = save_portfolio_snapshot(snapshot, settings=settings)
    print_kite_sync_result(profile, snapshot, artifact_path)
    return 0


async def handle_kite_login() -> int:
    settings = get_settings()
    async with build_kite_client(settings) as kite_client:
        payload, auth_url, auth_artifact = await kite_login(kite_client, settings=settings)
        initial_profile = await kite_get_profile(kite_client)
        if profile_requires_login(initial_profile):
            if auth_url:
                print(f"Complete Kite login in your browser: {auth_url}")
                print("Waiting for login confirmation...")
            profile = await wait_for_kite_login(kite_client, settings=settings)
        else:
            profile = initial_profile
        snapshot = await kite_get_portfolio(kite_client, settings=settings)
    portfolio_artifact = save_portfolio_snapshot(snapshot, settings=settings)
    print_kite_login_result(auth_artifact, auth_url, portfolio_artifact)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Artha portfolio research and rebalancing agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Full portfolio analysis and rebalancing report")
    run_parser.add_argument("--ticker", help="Run a single-stock deep dive only")
    run_parser.add_argument(
        "--rebalance-only",
        action="store_true",
        help="Skip fundamental analysis and compute only rebalancing actions",
    )

    subparsers.add_parser("holdings", help="Print the current holdings table without an LLM call")
    subparsers.add_parser("kite-login", help="Start Kite login, wait for completion on the same MCP session, and save a snapshot")
    subparsers.add_parser("kite-sync", help="Fetch profile and holdings from Kite MCP and save a local snapshot")
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
