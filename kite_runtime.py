from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import Settings, get_settings
from models import MFSnapshot, PortfolioSnapshot
from snapshot_store import (
    load_latest_mf_snapshot,
    load_latest_portfolio_snapshot,
    save_mf_snapshot,
    save_portfolio_snapshot,
)
from tools import (
    KiteMCPClient,
    ToolExecutionError,
    kite_get_mf_snapshot,
    kite_get_portfolio,
    kite_get_profile,
    kite_login,
    load_kite_server_definition,
    profile_requires_login,
    wait_for_kite_login,
)


logger = logging.getLogger(__name__)


@dataclass
class KiteSyncResult:
    profile: dict[str, object]
    portfolio_snapshot: PortfolioSnapshot
    portfolio_artifact: Path
    mf_snapshot: MFSnapshot
    mf_artifact: Path
    auth_url: str | None = None
    auth_artifact: Path | None = None


def build_kite_client(settings: Settings | None = None) -> KiteMCPClient:
    settings = settings or get_settings()
    return KiteMCPClient(
        load_kite_server_definition(settings),
        timeout_seconds=settings.kite_mcp_timeout_seconds,
    )


async def sync_kite_data(
    *,
    settings: Settings | None = None,
    auto_login: bool = True,
) -> KiteSyncResult:
    settings = settings or get_settings()

    async with build_kite_client(settings) as kite_client:
        return await sync_kite_data_with_client(
            kite_client,
            settings=settings,
            auto_login=auto_login,
        )


async def sync_kite_data_with_client(
    kite_client: KiteMCPClient,
    *,
    settings: Settings | None = None,
    auto_login: bool = True,
) -> KiteSyncResult:
    settings = settings or get_settings()
    profile = await kite_get_profile(kite_client)
    auth_url: str | None = None
    auth_artifact: Path | None = None

    if profile_requires_login(profile):
        if not auto_login:
            raise ToolExecutionError(
                "Kite session is not authenticated. Run `python main.py kite-login` first."
            )

        _, auth_url, auth_artifact = await kite_login(kite_client, settings=settings)
        logger.info("Kite login required. Complete authentication at: %s", auth_url or "login URL unavailable")
        profile = await wait_for_kite_login(kite_client, settings=settings)

    portfolio_snapshot, mf_snapshot = await _fetch_snapshots(kite_client, settings)
    portfolio_artifact = save_portfolio_snapshot(portfolio_snapshot, settings=settings)
    mf_artifact = save_mf_snapshot(mf_snapshot, settings=settings)
    return KiteSyncResult(
        profile=profile,
        portfolio_snapshot=portfolio_snapshot,
        portfolio_artifact=portfolio_artifact,
        mf_snapshot=mf_snapshot,
        mf_artifact=mf_artifact,
        auth_url=auth_url,
        auth_artifact=auth_artifact,
    )


def load_same_day_kite_sync_result(settings: Settings | None = None) -> KiteSyncResult | None:
    settings = settings or get_settings()
    try:
        portfolio_snapshot = load_latest_portfolio_snapshot(settings)
        mf_snapshot = load_latest_mf_snapshot(settings)
    except FileNotFoundError:
        return None

    today_utc = datetime.now(timezone.utc).date()
    if portfolio_snapshot.fetched_at.date() != today_utc:
        return None
    if mf_snapshot.fetched_at.date() != today_utc:
        return None

    return KiteSyncResult(
        profile={},
        portfolio_snapshot=portfolio_snapshot,
        portfolio_artifact=settings.kite_data_dir / "portfolio" / "latest_snapshot.json",
        mf_snapshot=mf_snapshot,
        mf_artifact=settings.kite_data_dir / "mf" / "latest_snapshot.json",
    )


async def _fetch_snapshots(
    kite_client: KiteMCPClient,
    settings: Settings,
) -> tuple[PortfolioSnapshot, MFSnapshot]:
    portfolio_snapshot = await kite_get_portfolio(kite_client, settings=settings)
    mf_snapshot = await kite_get_mf_snapshot(kite_client, settings=settings)
    return portfolio_snapshot, mf_snapshot
