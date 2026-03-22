from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from config import Settings, get_settings
from models import (
    CompanyAnalysisArtifact,
    CompanyDataCard,
    MFSnapshot,
    PortfolioReport,
    PortfolioSnapshot,
    ResearchDigest,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _write_text_atomic(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _write_model(model: BaseModel, path: Path) -> None:
    _write_text_atomic(model.model_dump_json(indent=2, by_alias=True), path)


def _write_payload(payload: dict, path: Path) -> None:
    _write_text_atomic(json.dumps(payload, indent=2, ensure_ascii=True), path)


def _timestamped_path(base_dir: Path, stem: str) -> Path:
    timestamp = model_now_timestamp()
    return base_dir / f"{timestamp}_{stem}.json"


def model_now_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def save_portfolio_snapshot(snapshot: PortfolioSnapshot, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    base_dir = settings.kite_data_dir / "portfolio"
    artifact = _timestamped_path(base_dir, "snapshot")
    latest = base_dir / "latest_snapshot.json"
    _write_model(snapshot, artifact)
    _write_model(snapshot, latest)
    return artifact


def save_mf_snapshot(snapshot: MFSnapshot, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    base_dir = settings.kite_data_dir / "mf"
    artifact = _timestamped_path(base_dir, "snapshot")
    latest = base_dir / "latest_snapshot.json"
    _write_model(snapshot, artifact)
    _write_model(snapshot, latest)
    return artifact


def load_latest_portfolio_snapshot(settings: Settings | None = None) -> PortfolioSnapshot:
    settings = settings or get_settings()
    path = settings.kite_data_dir / "portfolio" / "latest_snapshot.json"
    return PortfolioSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def load_latest_mf_snapshot(settings: Settings | None = None) -> MFSnapshot:
    settings = settings or get_settings()
    path = settings.kite_data_dir / "mf" / "latest_snapshot.json"
    return MFSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def save_research_digest(
    digest: ResearchDigest,
    per_holding_payloads: dict[str, dict],
    settings: Settings | None = None,
) -> tuple[Path, list[Path], Path]:
    settings = settings or get_settings()
    timestamp = model_now_timestamp()
    base_dir = settings.reports_dir / "research" / timestamp
    holdings_dir = base_dir / "holdings"
    per_holding_paths: list[Path] = []

    for identifier, payload in per_holding_payloads.items():
        safe_identifier = identifier.replace("/", "_").replace(" ", "_").upper()
        output_path = holdings_dir / f"{safe_identifier}.json"
        _write_payload(payload, output_path)
        per_holding_paths.append(output_path)

    digest_path = base_dir / "combined_digest.json"
    index_path = base_dir / "index.json"
    _write_model(digest, digest_path)
    _write_payload(
        {
            "generated_at": digest.generated_at.isoformat(),
            "digest_path": str(digest_path),
            "holding_reports": [str(path) for path in per_holding_paths],
        },
        index_path,
    )
    return digest_path, per_holding_paths, index_path


def save_report(report: PortfolioReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    filename = report.generated_at.strftime("%Y%m%d_%H%M%S_artha_report.json")
    output_path = reports_dir / filename
    _write_text_atomic(report.model_dump_json(indent=2), output_path)
    _update_reports_index(report, filename, reports_dir)
    return output_path


def _verdict_bucket_count(report: PortfolioReport) -> dict[str, int]:
    counts: dict[str, int] = {"BUY": 0, "HOLD": 0, "SELL": 0}
    for verdict in report.verdicts:
        v = verdict.verdict.value if hasattr(verdict.verdict, "value") else str(verdict.verdict)
        if v in {"STRONG_BUY", "BUY"}:
            counts["BUY"] += 1
        elif v in {"STRONG_SELL", "SELL"}:
            counts["SELL"] += 1
        else:
            counts["HOLD"] += 1
    return counts


def _update_reports_index(report: PortfolioReport, filename: str, reports_dir: Path) -> None:
    index_path = reports_dir / "index.json"
    try:
        existing: list[dict] = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []

    report_id = filename.removesuffix(".json")
    verdict_error_count = sum(1 for v in report.verdicts if v.error)
    entry: dict = {
        "id": report_id,
        "filename": filename,
        "generated_at": report.generated_at.isoformat(),
        "total_value": report.portfolio_snapshot.total_value,
        "verdict_counts": _verdict_bucket_count(report),
        "error_count": len(report.errors) + verdict_error_count,
    }
    # Remove any existing entry with the same id before appending
    existing = [e for e in existing if e.get("id") != report_id]
    existing.append(entry)
    _write_text_atomic(json.dumps(existing, indent=2, ensure_ascii=True), index_path)


def save_run_manifest(manifest: dict, reports_dir: Path) -> Path:
    manifests_dir = reports_dir / "manifests"
    run_id = str(manifest.get("run_id", model_now_timestamp()))
    output_path = manifests_dir / f"{run_id}_manifest.json"
    _write_payload(manifest, output_path)
    return output_path


def _judge_scores_path(ticker: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    safe_ticker = ticker.upper().replace("/", "_").replace(" ", "_")
    return settings.kite_data_dir / "companies" / f"{safe_ticker}_judge.json"


def save_judge_scores(
    ticker: str,
    quality_scores: dict | None,
    factual_scores: dict | None,
    combined_overall: float,
    passed: bool,
    settings: Settings | None = None,
) -> Path:
    path = _judge_scores_path(ticker, settings=settings)
    payload = {
        "ticker": ticker.upper(),
        "generated_at": datetime.now(UTC).isoformat(),
        "quality_scores": quality_scores,
        "factual_scores": factual_scores,
        "combined_overall": round(combined_overall, 2),
        "passed": passed,
    }
    _write_payload(payload, path)
    return path


def load_judge_scores(
    ticker: str,
    settings: Settings | None = None,
) -> dict | None:
    path = _judge_scores_path(ticker, settings=settings)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def company_analysis_path(ticker: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    safe_ticker = ticker.upper().replace("/", "_").replace(" ", "_")
    return settings.kite_data_dir / "companies" / f"{safe_ticker}.json"


def save_company_analysis_artifact(
    artifact: CompanyDataCard | CompanyAnalysisArtifact,
    settings: Settings | None = None,
) -> Path:
    path = company_analysis_path(artifact.ticker, settings=settings)
    _write_model(artifact, path)
    return path


def load_company_analysis_artifact(
    ticker: str,
    settings: Settings | None = None,
) -> CompanyDataCard | CompanyAnalysisArtifact:
    path = company_analysis_path(ticker, settings=settings)
    payload = json.loads(path.read_text(encoding="utf-8"))

    # Try CompanyDataCard first (new format)
    try:
        return CompanyDataCard.model_validate(payload)
    except Exception:
        pass

    # Fall back to CompanyAnalysisArtifact (old format) with migration
    migrated = False
    stock_snapshot = payload.get("report_card", {}).get("stock_snapshot", {})
    if isinstance(stock_snapshot, dict):
        if "high_52w" in stock_snapshot and "52w_high" not in stock_snapshot:
            stock_snapshot["52w_high"] = stock_snapshot.pop("high_52w")
            migrated = True
        if "low_52w" in stock_snapshot and "52w_low" not in stock_snapshot:
            stock_snapshot["52w_low"] = stock_snapshot.pop("low_52w")
            migrated = True

    artifact = CompanyAnalysisArtifact.model_validate(payload)
    if migrated:
        _write_model(artifact, path)
    return artifact
