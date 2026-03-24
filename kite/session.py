from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import Settings, get_settings
from providers.kite import KiteMCPClient
from providers.mcp_client import ToolExecutionError


def _artifact_path(settings: Settings, *parts: str) -> Path:
    path = settings.kite_data_dir.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def save_kite_artifact(payload: dict[str, Any], *, settings: Settings | None = None, category: str, stem: str) -> Path:
    settings = settings or get_settings()
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    artifact = _artifact_path(settings, category, f"{timestamp}_{stem}.json")
    artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    latest_artifact = _artifact_path(settings, category, f"latest_{stem}.json")
    latest_artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return artifact


def extract_auth_url(payload: Any) -> str | None:
    if isinstance(payload, str):
        match = re.search(r"https?://\S+", payload)
        return match.group(0).rstrip(").,]}>") if match else None
    if isinstance(payload, list):
        for item in payload:
            found = extract_auth_url(item)
            if found:
                return found
        return None
    if isinstance(payload, dict):
        for key in ("url", "login_url", "auth_url", "authorize_url", "redirect_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value.rstrip(").,]}>")
        for value in payload.values():
            found = extract_auth_url(value)
            if found:
                return found
    return None


async def kite_login(
    kite_client: KiteMCPClient, settings: Settings | None = None
) -> tuple[dict[str, Any], str | None, Path]:
    settings = settings or get_settings()
    raw_response = await kite_client.call_tool("login")
    payload = raw_response if isinstance(raw_response, dict) else {"raw_text": raw_response}
    auth_url = extract_auth_url(payload)
    if auth_url:
        payload["auth_url"] = auth_url
    return payload, auth_url, save_kite_artifact(payload, settings=settings, category="auth", stem="login")


async def kite_get_profile(kite_client: KiteMCPClient) -> dict[str, Any]:
    raw_response = await kite_client.call_tool("get_profile")
    return raw_response if isinstance(raw_response, dict) else {"raw_text": raw_response}


def profile_requires_login(profile: dict[str, Any]) -> bool:
    if not profile:
        return True
    raw_text = str(profile.get("raw_text", "")).lower()
    if "please log in first" in raw_text or "login tool" in raw_text:
        return True
    return not any(marker in profile for marker in ("user_id", "user_name", "email", "broker", "exchanges"))


async def wait_for_kite_login(kite_client: KiteMCPClient, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    deadline = time.monotonic() + settings.kite_login_timeout_seconds
    while time.monotonic() < deadline:
        profile = await kite_get_profile(kite_client)
        if not profile_requires_login(profile):
            return profile
        await asyncio.sleep(settings.kite_login_poll_interval_seconds)
    raise ToolExecutionError("Kite login did not complete before timeout. Finish the browser login and retry.")
