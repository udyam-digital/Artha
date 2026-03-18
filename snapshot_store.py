from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from config import Settings, get_settings
from models import MFSnapshot, PortfolioSnapshot, ResearchDigest


ModelT = TypeVar("ModelT", bound=BaseModel)


def _write_model(model: BaseModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def _write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _timestamped_path(base_dir: Path, stem: str) -> Path:
    timestamp = model_now_timestamp()
    return base_dir / f"{timestamp}_{stem}.json"


def model_now_timestamp() -> str:
    from datetime import datetime

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


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
