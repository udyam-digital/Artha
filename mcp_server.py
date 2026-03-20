"""Artha MCP server — exposes portfolio reports and analyst artifacts to Claude Desktop."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from application.reporting import (
    ReportNotFoundError,
    ReportParseError,
    get_latest_report,
    get_report_by_id,
    list_report_items,
)
from config import get_settings
from persistence.store import company_analysis_path, load_company_analysis_artifact

mcp = FastMCP("artha")
_ARTHA_ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@mcp.tool()
def list_artha_reports() -> str:
    """List all Artha portfolio reports, newest first.

    Returns a summary of each report: id, date, portfolio value, and verdict counts (BUY/HOLD/SELL).
    Use the id with get_artha_report() to fetch the full report.
    """
    settings = get_settings()
    items = list_report_items(settings)
    if not items:
        return "No Artha reports found."
    rows = []
    for item in items:
        counts = item.verdict_counts
        rows.append(
            f"- {item.id}  |  {item.generated_at.strftime('%Y-%m-%d %H:%M')}  "
            f"|  ₹{item.total_value:,.0f}  "
            f"|  BUY:{counts.get('BUY', 0)} HOLD:{counts.get('HOLD', 0)} SELL:{counts.get('SELL', 0)}"
            f"  errors:{item.error_count}"
        )
    return "\n".join(rows)


@mcp.tool()
def get_latest_artha_report() -> str:
    """Get the latest Artha portfolio report in full.

    Returns the complete PortfolioReport JSON including all stock verdicts,
    rebalancing actions, portfolio summary, and buy/sell totals.
    """
    settings = get_settings()
    try:
        report = get_latest_report(settings)
    except ReportNotFoundError:
        return "No Artha reports found. Run Artha first with run_artha_analysis()."
    except ReportParseError as exc:
        return f"Error reading report: {exc}"
    return report.model_dump_json(indent=2)


@mcp.tool()
def get_artha_report(report_id: str) -> str:
    """Get a specific Artha portfolio report by its id.

    Args:
        report_id: The report id as shown in list_artha_reports()
                   e.g. '20260318_155736_artha_report'
    """
    settings = get_settings()
    try:
        report = get_report_by_id(settings, report_id)
    except ReportNotFoundError:
        return f"Report '{report_id}' not found. Use list_artha_reports() to see available reports."
    except ReportParseError as exc:
        return f"Error reading report: {exc}"
    return report.model_dump_json(indent=2)


# ---------------------------------------------------------------------------
# Analyst artifacts
# ---------------------------------------------------------------------------


@mcp.tool()
def list_analyst_artifacts() -> str:
    """List all cached company analyst artifacts (one per ticker Artha has analysed).

    Returns ticker names and how recently each was analysed.
    Use get_analyst_artifact(ticker) to read the full report card.
    """
    settings = get_settings()
    companies_dir = settings.kite_data_dir / "companies"
    files = sorted(companies_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return "No analyst artifacts found. Run Artha to generate them."
    rows = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            generated_at = data.get("generated_at", "unknown")
            ticker = data.get("ticker", f.stem)
            verdict = data.get("report_card", {}).get("final_verdict", {}).get("verdict", "?")
            rows.append(f"- {ticker}  |  verdict: {verdict}  |  analysed: {generated_at}")
        except Exception:
            rows.append(f"- {f.stem}  (unreadable)")
    return "\n".join(rows)


@mcp.tool()
def get_analyst_artifact(ticker: str) -> str:
    """Get the full analyst report card for a specific stock.

    Returns the complete CompanyAnalysisArtifact including thesis, growth engine,
    quality scores, valuation, timing, risk matrix, action plan, and final verdict.

    Args:
        ticker: NSE ticker symbol e.g. 'KPITTECH', 'INFY', 'RELIANCE'
    """
    try:
        artifact = load_company_analysis_artifact(ticker.upper())
    except FileNotFoundError:
        return (
            f"No analyst artifact found for {ticker.upper()}. "
            "Run run_artha_analyst(ticker) to generate one."
        )
    except Exception as exc:
        return f"Error reading artifact for {ticker.upper()}: {exc}"
    return artifact.model_dump_json(indent=2, by_alias=True)


# ---------------------------------------------------------------------------
# Trigger Artha
# ---------------------------------------------------------------------------


@mcp.tool()
def run_artha_analysis(ticker: str = "", rebalance_only: bool = False) -> str:
    """Trigger an Artha analysis run. This will call Claude Haiku and may take 1-5 minutes.

    - No args: full portfolio analysis of all holdings
    - ticker='INFY': single stock deep dive for that ticker
    - rebalance_only=True: drift math only, no LLM calls

    Args:
        ticker: Optional NSE ticker for a single-stock run e.g. 'KPITTECH'
        rebalance_only: If True, skip LLM and run rebalancing math only
    """
    cmd = [sys.executable, str(_ARTHA_ROOT / "main.py"), "run"]
    if ticker:
        cmd += ["--ticker", ticker.upper()]
    if rebalance_only:
        cmd += ["--rebalance-only"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_ARTHA_ROOT),
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return "Artha analysis timed out after 10 minutes."
    except Exception as exc:
        return f"Failed to launch Artha: {exc}"

    if result.returncode != 0:
        stderr = result.stderr[-2000:] if result.stderr else ""
        return f"Artha run failed (exit {result.returncode}).\n{stderr}"

    # After a successful run, return the latest report summary
    settings = get_settings()
    try:
        report = get_latest_report(settings)
        counts: dict[str, int] = {"BUY": 0, "HOLD": 0, "SELL": 0}
        for v in report.verdicts:
            bucket = "BUY" if v.verdict.value in ("BUY", "STRONG_BUY") else (
                "SELL" if v.verdict.value in ("SELL", "STRONG_SELL") else "HOLD"
            )
            counts[bucket] += 1
        return (
            f"Artha run complete.\n"
            f"Report: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Portfolio value: ₹{report.portfolio_snapshot.total_value:,.0f}\n"
            f"Verdicts — BUY:{counts['BUY']} HOLD:{counts['HOLD']} SELL:{counts['SELL']}\n"
            f"Summary: {report.portfolio_summary}"
        )
    except Exception:
        return "Artha run completed. Use get_latest_artha_report() to read the results."


if __name__ == "__main__":
    mcp.run()
