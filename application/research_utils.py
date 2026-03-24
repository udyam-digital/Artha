from __future__ import annotations

import json
import re
from typing import Any


def extract_text(response: Any) -> str:
    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    return "\n".join(text_parts).strip()


def extract_tagged_json(raw_text: str, tag: str, identifier: str, error_type: type[Exception]) -> dict[str, Any]:
    match = re.search(rf"<{tag}>\s*(\{{.*?\}})\s*</{tag}>", raw_text, re.DOTALL)
    if not match:
        raise error_type(f"Research output for {identifier} did not contain <{tag}> JSON tags.")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise error_type(f"Research output for {identifier} was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise error_type(f"Research output for {identifier} was not a JSON object.")
    return payload


def coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def unique_payload_key(kind: str, identifier: str, existing_payloads: dict[str, dict[str, Any]]) -> str:
    base_key = f"{kind}_{identifier}".replace("/", "_").replace(" ", "_").upper()
    candidate = base_key
    suffix = 2
    while candidate in existing_payloads:
        candidate = f"{base_key}_{suffix}"
        suffix += 1
    return candidate
