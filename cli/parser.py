from __future__ import annotations

import argparse


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
    analyst_parser = subparsers.add_parser(
        "analyst", help="Run one standalone analyst report card without Kite or Artha summary"
    )
    analyst_parser.add_argument("--ticker", required=True, help="Ticker to analyse")
    analyst_parser.add_argument("--exchange", default="NSE", help="Exchange for standalone analyst mode, default NSE")
    compare_parser = subparsers.add_parser(
        "compare-providers", help="Fetch Yahoo Finance and NSE India data into separate JSON files"
    )
    compare_parser.add_argument("--ticker", required=True, help="Ticker to fetch")
    compare_parser.add_argument("--exchange", default="NSE", help="Exchange suffix for provider exports, default NSE")
    subparsers.add_parser(
        "kite-login", help="Start Kite login, wait for completion on the same MCP session, and save a snapshot"
    )
    subparsers.add_parser("kite-sync", help="Fetch fresh equity and MF snapshots from Kite MCP and save them locally")
    subparsers.add_parser("rebalance", help="Generate a rebalancing report from the latest saved local equity snapshot")
    subparsers.add_parser("research", help="Run deep web research on the latest saved equity and MF snapshots")
    usage_parser = subparsers.add_parser("usage-report", help="Print recent historical LLM usage summaries")
    usage_parser.add_argument("--last", type=int, default=10, help="Number of recent runs to show")
    return parser
