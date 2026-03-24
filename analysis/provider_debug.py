from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from config import Settings
from kite.tools import get_nse_india_provider_payload, get_yfinance_provider_payload


def _provider_compare_dir(config: Settings) -> Path:
    path = config.kite_data_dir / "provider_compare"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def export_provider_comparison_files(
    ticker: str,
    *,
    exchange: str,
    config: Settings,
) -> list[Path]:
    normalized_ticker = str(ticker).strip().upper()
    fetched_at = datetime.now(UTC).isoformat()
    yahoo_task = get_yfinance_provider_payload(normalized_ticker)
    nse_task = get_nse_india_provider_payload(normalized_ticker)
    yahoo_payload, nse_payload = await asyncio.gather(yahoo_task, nse_task)

    provider_payloads = (
        ("yfinance", yahoo_payload),
        ("nse_india", nse_payload),
    )
    output_dir = _provider_compare_dir(config)
    saved_paths: list[Path] = []
    for provider_name, payload in provider_payloads:
        enriched_payload = {
            "provider": provider_name,
            "ticker": normalized_ticker,
            "exchange": exchange.upper(),
            "fetched_at": fetched_at,
            **payload,
        }
        path = output_dir / f"{normalized_ticker}_{provider_name}.json"
        path.write_text(json.dumps(enriched_payload, indent=2, ensure_ascii=True), encoding="utf-8")
        saved_paths.append(path)
    return saved_paths
