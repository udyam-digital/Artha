from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import URLError

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from application.orchestrator import (
    RunEvent,
    build_rebalance_only_report,
    run_full_analysis,
    run_single_company_analysis,
)
from application.reporting import (
    HoldingNotFoundError,
    ReportListItem,
    ReportNotFoundError,
    ReportParseError,
    find_holding_in_latest_report,
    get_latest_report,
    get_report_by_id,
    list_report_items,
)
from config import Settings, get_settings
from kite.runtime import build_kite_client, sync_kite_data
from kite.tools import kite_get_portfolio, kite_get_profile, kite_login, profile_requires_login
from models import MFSnapshot, PortfolioReport, PortfolioSnapshot
from persistence.store import load_latest_mf_snapshot, load_latest_portfolio_snapshot, save_report
from reliability import FullRunFailed

APP_VERSION = "1.0"
STREAM_ERROR_CODE = 1001
REPORT_PARSE_ERROR_DETAIL = "Internal server error while parsing report."
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    artha_version: str


class HoldingsResponse(PortfolioSnapshot):
    mf_snapshot: MFSnapshot | None = None
    live_status: str = "live"
    live_error: dict[str, str | None] | None = None


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
        mf_snapshot = _load_latest_mf_snapshot_or_none(settings)
        try:
            async with build_kite_client(settings) as kite_client:
                await _ensure_authenticated(kite_client, settings)
                snapshot = await kite_get_portfolio(kite_client, settings=settings)
        except HTTPException as exc:
            snapshot = _load_latest_portfolio_snapshot_or_none(settings)
            if snapshot is None:
                raise
            return HoldingsResponse(
                **snapshot.model_dump(),
                mf_snapshot=mf_snapshot,
                live_status="fallback",
                live_error=_http_exception_to_live_error(exc),
            )
        except (OSError, TimeoutError, URLError) as exc:
            snapshot = _load_latest_portfolio_snapshot_or_none(settings)
            if snapshot is None:
                raise HTTPException(status_code=503, detail="Failed to reach Kite MCP.") from exc
            return HoldingsResponse(
                **snapshot.model_dump(),
                mf_snapshot=mf_snapshot,
                live_status="fallback",
                live_error={
                    "message": "Live Kite holdings unavailable. Showing the latest saved snapshot.",
                    "login_url": None,
                },
            )
        return HoldingsResponse(**snapshot.model_dump(), mf_snapshot=mf_snapshot, live_status="live", live_error=None)

    @app.get("/api/mf-holdings", response_model=MFSnapshot)
    async def mf_holdings() -> MFSnapshot:
        settings = get_settings()
        try:
            return load_latest_mf_snapshot(settings)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="No MF snapshot found.") from exc

    @app.get("/api/reports", response_model=list[ReportListItem])
    async def reports() -> list[ReportListItem]:
        return list_report_items(get_settings())

    @app.get("/api/reports/latest", response_model=PortfolioReport)
    async def latest_report() -> PortfolioReport:
        settings = get_settings()
        try:
            return get_latest_report(settings)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ReportParseError as exc:
            _raise_report_parse_http_error("latest_report", exc)

    @app.get("/api/reports/{report_id}", response_model=PortfolioReport)
    async def report_detail(report_id: str) -> PortfolioReport:
        settings = get_settings()
        try:
            return get_report_by_id(settings, report_id)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ReportParseError as exc:
            _raise_report_parse_http_error("report_detail", exc)

    @app.post("/api/run")
    async def run_artha(request: RunRequest) -> StreamingResponse:
        settings = get_settings()
        if not request.ticker:
            try:
                async with build_kite_client(settings) as kite_client:
                    await _ensure_authenticated(kite_client, settings)
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("Run preflight failed")
                raise HTTPException(status_code=503, detail="Failed to initialize the live Kite session.") from exc
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
        try:
            holding = find_holding_in_latest_report(settings, ticker)
        except ReportNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ReportParseError as exc:
            _raise_report_parse_http_error("price_history", exc)
        except HoldingNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        async with build_kite_client(settings) as kite_client:
            await _ensure_authenticated(kite_client, settings)
            raw_history = await kite_client.call_tool(
                "get_historical_data",
                {
                    "instrument_token": holding.instrument_token,
                    "interval": "day",
                    "from_date": (datetime.now(UTC) - timedelta(days=365)).date().isoformat(),
                    "to_date": datetime.now(UTC).date().isoformat(),
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


def _load_latest_portfolio_snapshot_or_none(settings: Settings) -> PortfolioSnapshot | None:
    try:
        return load_latest_portfolio_snapshot(settings)
    except FileNotFoundError:
        return None


def _raise_report_parse_http_error(route_name: str, exc: ReportParseError) -> None:
    logger.exception("Report parsing failed in %s", route_name)
    raise HTTPException(status_code=500, detail=REPORT_PARSE_ERROR_DETAIL) from exc


def _http_exception_to_live_error(exc: HTTPException) -> dict[str, str | None]:
    detail = exc.detail
    if isinstance(detail, dict):
        return {
            "message": str(
                detail.get("message") or "Live Kite holdings unavailable. Showing the latest saved snapshot."
            ),
            "login_url": str(detail.get("login_url")) if detail.get("login_url") else None,
        }
    if isinstance(detail, str):
        return {"message": detail, "login_url": None}
    return {"message": "Live Kite holdings unavailable. Showing the latest saved snapshot.", "login_url": None}


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
    yield _sse("status", {"state": "started"})

    if request.rebalance_only:
        try:
            sync_result = await sync_kite_data(settings=settings)
            report, _ = build_rebalance_only_report(sync_result.portfolio_snapshot)
            report_path = save_report(report, settings.reports_dir)
            yield _sse("complete", {"report_id": report_path.stem, "report_path": str(report_path)})
        except Exception:
            logger.exception("Rebalance-only run failed")
            yield _safe_error_sse(message="Rebalance-only run failed.", report_path=None, phase="rebalance")
        return

    queue: asyncio.Queue[RunEvent] = asyncio.Queue()

    def on_event(event: RunEvent) -> None:
        queue.put_nowait(event)

    run_task = asyncio.create_task(_run_and_save(request, settings, on_event))

    try:
        while not run_task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield _sse_from_run_event(event)
            except TimeoutError:
                continue

        while not queue.empty():
            yield _sse_from_run_event(queue.get_nowait())

        try:
            report_path = run_task.result()
            yield _sse("complete", {"report_id": report_path.stem, "report_path": str(report_path)})
        except FullRunFailed as exc:
            yield _sse("error", {"message": exc.message, "phase": exc.phase, "return_code": 1, "report_path": None})
        except Exception:
            logger.exception("Full analysis stream failed")
            yield _safe_error_sse(message="Full analysis failed.", report_path=None)
    finally:
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task


async def _run_and_save(request: RunRequest, settings: Settings, event_callback) -> Path:
    if request.ticker:
        report = await run_single_company_analysis(
            settings=settings,
            ticker=request.ticker,
            exchange=request.exchange,
        )
    else:
        report = await run_full_analysis(settings, event_callback=event_callback)
    return save_report(report, settings.reports_dir)


def _sse_from_run_event(event: RunEvent) -> str:
    if event["type"] == "phase":
        return _sse("phase", {"phase": event["phase"], "label": event["label"], "total": event["total"]})
    return _sse(
        "progress",
        {
            "completed": event["completed"],
            "total": event["total"],
            "ticker": event["ticker"],
            "verdict": event["verdict"],
            "confidence": event["confidence"],
            "thesis_intact": event["thesis_intact"],
            "pnl_pct": event["pnl_pct"],
            "duration_seconds": event["duration_seconds"],
            "bull_case": event["bull_case"],
            "red_flags": event["red_flags"],
        },
    )


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _safe_error_sse(*, message: str, report_path: str | None, phase: str | None = None) -> str:
    payload: dict[str, object] = {
        "message": message,
        "error_code": STREAM_ERROR_CODE,
        "return_code": 1,
        "report_path": report_path,
    }
    if phase is not None:
        payload["phase"] = phase
    return _sse("error", payload)
