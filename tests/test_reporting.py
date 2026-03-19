from pathlib import Path

import pytest

from application.reporting import ReportNotFoundError, get_report_by_id
from config import Settings


def make_settings(tmp_path: Path) -> Settings:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(reports_dir),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


def test_get_report_by_id_blocks_path_traversal(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    with pytest.raises(ReportNotFoundError):
        get_report_by_id(settings, "../secret")
