from __future__ import annotations

import asyncio
import logging
import sys

from anthropic import AsyncAnthropic  # noqa: F401

import cli.commands as commands
from analysis import export_provider_comparison_files, generate_yfinance_only_company_artifact  # noqa: F401
from application.orchestrator import run_full_analysis  # noqa: F401 — re-exported for tests
from cli.commands import (  # noqa: F401 — re-exported for callers that import from main
    handle_holdings,
    handle_kite_login,
    handle_kite_sync,
    handle_rebalance,
    handle_research,
    handle_usage_report,
)
from cli.display import (  # noqa: F401 — re-exported
    print_company_artifact,
    print_holdings,
    print_kite_login_result,
    print_kite_sync_result,
    print_rebalance_report,
    print_report,
    print_research_result,
    print_run_failure,
    print_single_verdict,
)
from cli.format import (  # noqa: F401 — re-exported
    _thesis_text,
    _verdict_to_action_text,
    format_rupees,
)
from cli.parser import build_parser
from config import configure_logging, get_settings
from kite.runtime import load_same_day_kite_sync_result  # noqa: F401 — re-exported for tests
from kite.tools import ToolExecutionError
from observability.telemetry import initialize_telemetry, shutdown_telemetry

logger = logging.getLogger(__name__)


async def handle_run(args):
    commands.get_settings = get_settings
    commands.load_same_day_kite_sync_result = load_same_day_kite_sync_result
    commands.run_full_analysis = run_full_analysis
    return await commands.handle_run(args)


async def handle_analyst(args):
    commands.AsyncAnthropic = AsyncAnthropic
    commands.generate_yfinance_only_company_artifact = generate_yfinance_only_company_artifact
    commands.get_settings = get_settings
    return await commands.handle_analyst(args)


async def handle_compare_providers(args):
    commands.export_provider_comparison_files = export_provider_comparison_files
    commands.get_settings = get_settings
    return await commands.handle_compare_providers(args)


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    initialize_telemetry(settings)

    try:
        if args.command == "run":
            return await handle_run(args)
        if args.command == "holdings":
            return await handle_holdings()
        if args.command == "analyst":
            return await handle_analyst(args)
        if args.command == "compare-providers":
            return await handle_compare_providers(args)
        if args.command == "kite-login":
            return await handle_kite_login()
        if args.command == "kite-sync":
            return await handle_kite_sync()
        if args.command == "rebalance":
            return await handle_rebalance()
        if args.command == "research":
            return await handle_research()
        if args.command == "usage-report":
            return await handle_usage_report(args)
        parser.error("Unknown command")
        return 2
    except ToolExecutionError as exc:
        logger.error(str(exc))
        return 1
    except Exception:
        logger.exception("Artha failed")
        return 1
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    sys.exit(asyncio.run(async_main()))
