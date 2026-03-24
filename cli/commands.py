from __future__ import annotations

import argparse
import logging
import time

from anthropic import AsyncAnthropic

from analysis import export_provider_comparison_files, generate_yfinance_only_company_artifact
from application.orchestrator import (
    RunEvent,
    build_rebalance_only_report,
    run_full_analysis,
    run_single_company_analysis,
)
from application.research import DeepResearchOrchestrator
from cli.display import (
    print_company_artifact,
    print_kite_login_result,
    print_kite_sync_result,
    print_rebalance_report,
    print_report,
    print_research_result,
    print_run_failure,
)
from config import get_settings
from kite.runtime import load_same_day_kite_sync_result, sync_kite_data
from models import Holding
from observability.usage import format_usage_summary, load_recent_run_summaries, usage_run
from persistence.store import load_latest_portfolio_snapshot, save_report
from reliability import FullRunFailed

logger = logging.getLogger(__name__)


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

    def event_callback(event: RunEvent) -> None:
        if event["type"] == "phase":
            logger.info("[%s] %s", event["phase"].upper(), event["label"])
        elif event["type"] == "analyst_complete":
            print(
                f"[{event['completed']}/{event['total']}] {event['ticker']:<10} "
                f"✓ {event['verdict']:<9} ({event['duration_seconds']:.1f}s)"
            )

    with usage_run(settings=settings, command="run") as usage_summary:
        try:
            cached_sync_result = load_same_day_kite_sync_result(settings)
            if cached_sync_result is not None:
                print("Using today's saved Kite snapshots. Skipping fresh Kite login and sync.")
            report = await run_full_analysis(
                settings,
                event_callback=event_callback,
                sync_result=cached_sync_result,
            )
        except FullRunFailed as exc:
            print()
            print_run_failure(exc, usage_summary)
            return 1

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
    from cli.display import print_holdings

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
    print_kite_login_result(
        result.auth_artifact or result.portfolio_artifact, result.auth_url, result.portfolio_artifact
    )
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


async def handle_usage_report(args: argparse.Namespace) -> int:
    settings = get_settings()
    summaries = load_recent_run_summaries(settings, limit=args.last)
    if not summaries:
        print("No historical run summaries found.")
        print(f"Expected summary log path: {settings.llm_usage_dir / 'run_summaries.jsonl'}")
        return 0

    from observability.usage import format_run_summary

    print(f"Showing {len(summaries)} most recent run(s):")
    print()
    for summary in summaries:
        print(format_run_summary(summary))
    print()
    print(f"Run summary log: {settings.llm_usage_dir / 'run_summaries.jsonl'}")
    return 0


async def handle_analyst(args: argparse.Namespace) -> int:
    settings = get_settings()
    holding = build_standalone_holding(args.ticker, exchange=args.exchange)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    with usage_run(settings=settings, command=f"analyst --ticker {args.ticker.upper()}") as usage_summary:
        artifact = await generate_yfinance_only_company_artifact(
            holding=holding,
            client=client,
            config=settings,
        )

    print_company_artifact(artifact)
    print()
    print(format_usage_summary(usage_summary))
    print(f"LLM usage log saved to: {usage_summary.usage_path}")
    return 0


async def handle_compare_providers(args: argparse.Namespace) -> int:
    settings = get_settings()
    paths = await export_provider_comparison_files(
        args.ticker,
        exchange=args.exchange,
        config=settings,
    )
    print(f"Provider comparison files for {args.ticker.upper()} (Yahoo Finance + NSE India):")
    for path in paths:
        print(path)
    return 0
