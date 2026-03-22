from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from config import Settings
from models import Holding, PortfolioReport, Verdict


class ReportAccessError(RuntimeError):
    pass


class ReportNotFoundError(ReportAccessError):
    pass


class ReportParseError(ReportAccessError):
    pass


class HoldingNotFoundError(ReportAccessError):
    pass


class ReportListItem(BaseModel):
    id: str
    filename: str
    generated_at: datetime
    total_value: float
    error_count: int
    verdict_counts: dict[str, int]


def list_report_items(settings: Settings) -> list[ReportListItem]:
    fast_path_items = _list_report_items_from_index(settings)
    if fast_path_items is not None:
        return fast_path_items
    # Fallback: full file reparsing
    items: list[ReportListItem] = []
    for report_path in _list_report_files(settings):
        try:
            report = load_report(report_path)
        except ReportAccessError:
            continue
        items.append(_report_to_list_item(report_path, report))
    return items


def _list_report_items_from_index(settings: Settings) -> list[ReportListItem] | None:
    index_path = settings.reports_dir / "index.json"
    if not index_path.exists():
        return None
    try:
        raw: list[dict] = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return None
        items: list[ReportListItem] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                items.append(
                    ReportListItem(
                        id=str(entry["id"]),
                        filename=str(entry["filename"]),
                        generated_at=datetime.fromisoformat(str(entry["generated_at"])),
                        total_value=float(entry["total_value"]),
                        error_count=int(entry["error_count"]),
                        verdict_counts={str(k): int(v) for k, v in entry.get("verdict_counts", {}).items()},
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        items.sort(key=lambda x: x.generated_at, reverse=True)
        return items
    except Exception:
        return None


def get_latest_report(settings: Settings) -> PortfolioReport:
    return load_report(_get_latest_report_path(settings))


def get_report_by_id(settings: Settings, report_id: str) -> PortfolioReport:
    return load_report(_resolve_report_path(settings, report_id))


def find_holding_in_latest_report(settings: Settings, ticker: str) -> Holding:
    report = get_latest_report(settings)
    target = ticker.upper()
    for holding in report.portfolio_snapshot.holdings:
        if holding.tradingsymbol == target:
            return holding
    raise HoldingNotFoundError(f"Ticker '{target}' not found in the latest report.")


def load_report(report_path: Path) -> PortfolioReport:
    try:
        return PortfolioReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReportNotFoundError("Report file not found.") from exc
    except Exception as exc:  # pragma: no cover - defensive parsing guard
        raise ReportParseError(f"Failed to parse report {report_path.name}.") from exc


def _report_to_list_item(report_path: Path, report: PortfolioReport) -> ReportListItem:
    verdict_counts = {"BUY": 0, "HOLD": 0, "SELL": 0}
    verdict_error_count = 0
    for verdict in report.verdicts:
        if verdict.error:
            verdict_error_count += 1
        verdict_counts[_verdict_bucket(verdict.verdict)] += 1
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
        raise ReportNotFoundError("No Artha reports found.")
    return report_files[0]


def _resolve_report_path(settings: Settings, report_id: str) -> Path:
    if Path(report_id).name != report_id or report_id.endswith(".json"):
        raise ReportNotFoundError(f"Report '{report_id}' not found.")

    base_dir = settings.reports_dir.resolve()
    report_path = (base_dir / f"{report_id}.json").resolve()
    if report_path.parent != base_dir or not report_path.is_file():
        raise ReportNotFoundError(f"Report '{report_id}' not found.")
    return report_path
