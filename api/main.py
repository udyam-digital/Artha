from __future__ import annotations

import asyncio
import json
import re
import sys
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import Settings, get_settings
from kite_runtime import build_kite_client
from models import Holding, MFSnapshot, PortfolioReport, PortfolioSnapshot, Verdict
from snapshot_store import load_latest_mf_snapshot
from tools import kite_get_portfolio, kite_get_profile, kite_login, profile_requires_login


APP_VERSION = "1.0"
ROOT_DIR = Path(__file__).resolve().parents[1]


class HealthResponse(BaseModel):
    status: str
    artha_version: str


class HoldingsResponse(PortfolioSnapshot):
    mf_snapshot: MFSnapshot | None = None


class ReportListItem(BaseModel):
    id: str
    filename: str
    generated_at: datetime
    total_value: float
    error_count: int
    verdict_counts: dict[str, int]


class RunRequest(BaseModel):
    rebalance_only: bool = False
    ticker: str | None = None
    exchange: str = "NSE"


class PriceHistoryCandle(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def create_app() -> FastAPI:
    app = FastAPI(title="Artha API", version=APP_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", artha_version=APP_VERSION)

    @app.get("/api/holdings", response_model=HoldingsResponse)
    async def holdings() -> HoldingsResponse:
        settings = get_settings()
        async with build_kite_client(settings) as kite_client:
            await _ensure_authenticated(kite_client, settings)
            snapshot = await kite_get_portfolio(kite_client, settings=settings)
        mf_snapshot = _load_latest_mf_snapshot_or_none(settings)
        return HoldingsResponse(**snapshot.model_dump(), mf_snapshot=mf_snapshot)

    @app.get("/api/reports", response_model=list[ReportListItem])
    async def reports() -> list[ReportListItem]:
        items: list[ReportListItem] = []
        for report_path in _list_report_files(get_settings()):
            item = _report_to_list_item(report_path)
            if item is not None:
                items.append(item)
        return items

    @app.get("/api/reports/latest", response_model=PortfolioReport)
    async def latest_report() -> PortfolioReport:
        report_path = _get_latest_report_path(get_settings())
        return _load_report(report_path)

    @app.get("/api/reports/{report_id}", response_model=PortfolioReport)
    async def report_detail(report_id: str) -> PortfolioReport:
        report_path = _resolve_report_path(get_settings(), report_id)
        return _load_report(report_path)

    @app.post("/api/run")
    async def run_artha(request: RunRequest) -> StreamingResponse:
        settings = get_settings()
        async with build_kite_client(settings) as kite_client:
            await _ensure_authenticated(kite_client, settings)
        return StreamingResponse(
            _stream_run(request, settings),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/price-history/{ticker}", response_model=list[PriceHistoryCandle])
    async def price_history(ticker: str) -> list[PriceHistoryCandle]:
        settings = get_settings()
        holding = _find_holding_in_latest_report(settings, ticker)
        async with build_kite_client(settings) as kite_client:
            await _ensure_authenticated(kite_client, settings)
            raw_history = await kite_client.call_tool(
                "get_historical_data",
                {
                    "instrument_token": holding.instrument_token,
                    "interval": "day",
                    "from_date": (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat(),
                    "to_date": datetime.now(timezone.utc).date().isoformat(),
                },
            )
        candles = _normalize_candles(raw_history)
        if not candles:
            raise HTTPException(status_code=404, detail=f"No price history available for {ticker.upper()}.")
        return candles

    return app


app = create_app()


async def _ensure_authenticated(kite_client, settings: Settings) -> None:
    try:
        profile = await kite_get_profile(kite_client)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Failed to reach Kite MCP.") from exc

    if not profile_requires_login(profile):
        return

    login_url: str | None = None
    with suppress(Exception):
        _, login_url, _ = await kite_login(kite_client, settings=settings)

    raise HTTPException(
        status_code=401,
        detail={
            "message": "Kite session expired. Reconnect and retry.",
            "login_url": login_url,
        },
    )


def _load_latest_mf_snapshot_or_none(settings: Settings) -> MFSnapshot | None:
    try:
        return load_latest_mf_snapshot(settings)
    except FileNotFoundError:
        return None


def _report_to_list_item(report_path: Path) -> ReportListItem | None:
    try:
        report = _load_report(report_path)
    except HTTPException:
        return None
    verdict_counts = {"BUY": 0, "HOLD": 0, "SELL": 0}
    verdict_error_count = 0
    for verdict in report.verdicts:
        if verdict.error:
            verdict_error_count += 1
        bucket = _verdict_bucket(verdict.verdict)
        verdict_counts[bucket] += 1
    return ReportListItem(
        id=report_path.stem,
        filename=report_path.name,
        generated_at=report.generated_at,
        total_value=report.portfolio_snapshot.total_value,
        error_count=len(report.errors) + verdict_error_count,
        verdict_counts=verdict_counts,
    )


def _verdict_bucket(verdict: Verdict) -> str:
    if verdict in {Verdict.BUY, Verdict.STRONG_BUY}:
        return "BUY"
    if verdict in {Verdict.SELL, Verdict.STRONG_SELL}:
        return "SELL"
    return "HOLD"


def _list_report_files(settings: Settings) -> list[Path]:
    report_files = sorted(settings.reports_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [path for path in report_files if path.is_file()]


def _get_latest_report_path(settings: Settings) -> Path:
    report_files = _list_report_files(settings)
    if not report_files:
        raise HTTPException(status_code=404, detail="No Artha reports found.")
    return report_files[0]


def _resolve_report_path(settings: Settings, report_id: str) -> Path:
    report_path = settings.reports_dir / f"{report_id}.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")
    return report_path


def _load_report(report_path: Path) -> PortfolioReport:
    try:
        return PortfolioReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Report file not found.") from exc
    except Exception as exc:  # pragma: no cover - defensive parsing guard
        raise HTTPException(status_code=500, detail=f"Failed to parse report {report_path.name}.") from exc


def _find_holding_in_latest_report(settings: Settings, ticker: str) -> Holding:
    report = _load_report(_get_latest_report_path(settings))
    target = ticker.upper()
    for holding in report.portfolio_snapshot.holdings:
        if holding.tradingsymbol == target:
            return holding
    raise HTTPException(status_code=404, detail=f"Ticker '{target}' not found in the latest report.")


def _normalize_candles(raw_history: object) -> list[PriceHistoryCandle]:
    payload: list[object] = []
    if isinstance(raw_history, dict):
        for key in ("candles", "data", "items"):
            value = raw_history.get(key)
            if isinstance(value, list):
                payload = value
                break
    elif isinstance(raw_history, list):
        payload = raw_history

    normalized: list[PriceHistoryCandle] = []
    for item in payload:
        if isinstance(item, dict):
            date_value = item.get("date") or item.get("timestamp")
            normalized.append(
                PriceHistoryCandle(
                    date=str(date_value),
                    open=float(item.get("open", 0.0) or 0.0),
                    high=float(item.get("high", 0.0) or 0.0),
                    low=float(item.get("low", 0.0) or 0.0),
                    close=float(item.get("close", 0.0) or 0.0),
                    volume=float(item.get("volume", 0.0) or 0.0),
                )
            )
        elif isinstance(item, list) and len(item) >= 6:
            normalized.append(
                PriceHistoryCandle(
                    date=str(item[0]),
                    open=float(item[1] or 0.0),
                    high=float(item[2] or 0.0),
                    low=float(item[3] or 0.0),
                    close=float(item[4] or 0.0),
                    volume=float(item[5] or 0.0),
                )
            )
    return normalized


async def _stream_run(request: RunRequest, settings: Settings) -> AsyncIterator[str]:
    before_latest = _latest_report_name_or_none(settings)
    command = [sys.executable, "main.py", "run"]
    if request.rebalance_only:
        command.append("--rebalance-only")
    if request.ticker:
        command.extend(["--ticker", request.ticker.upper()])
    if request.exchange:
        command.extend(["--exchange", request.exchange.upper()])

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(ROOT_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    yield _sse("status", {"state": "started"})
    assert process.stdout is not None
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            yield _sse("log", {"line": text})
            progress = _parse_progress_line(text)
            if progress:
                yield _sse("progress", progress)

    return_code = await process.wait()
    report_path = _find_new_report_path(settings, before_latest)
    if return_code != 0:
        yield _sse(
            "error",
            {
                "message": "Artha run failed.",
                "return_code": return_code,
                "report_path": str(report_path) if report_path else None,
            },
        )
        return

    yield _sse(
        "complete",
        {
            "report_id": report_path.stem if report_path else None,
            "report_path": str(report_path) if report_path else None,
        },
    )


def _latest_report_name_or_none(settings: Settings) -> str | None:
    report_files = _list_report_files(settings)
    return report_files[0].name if report_files else None


def _find_new_report_path(settings: Settings, previous_latest: str | None) -> Path | None:
    report_files = _list_report_files(settings)
    if not report_files:
        return None
    if previous_latest is None:
        return report_files[0]
    if report_files[0].name != previous_latest:
        return report_files[0]
    return settings.reports_dir / previous_latest


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _parse_progress_line(line: str) -> dict[str, object] | None:
    match = re.search(
        r"\[(?P<completed>\d+)/(?P<total>\d+)\]\s+(?P<ticker>[A-Z0-9]+)\s+✓\s+(?P<verdict>[A-Z_]+)\s+\((?P<seconds>[0-9.]+)s\)",
        line,
    )
    if not match:
        return None
    return {
        "completed": int(match.group("completed")),
        "total": int(match.group("total")),
        "ticker": match.group("ticker"),
        "verdict": match.group("verdict"),
        "duration_seconds": float(match.group("seconds")),
    }
