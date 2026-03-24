from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_USD = Decimal("0.000001")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok_usd: Decimal
    output_per_mtok_usd: Decimal
    cache_read_per_mtok_usd: Decimal = _ZERO
    cache_creation_per_mtok_usd: Decimal = _ZERO
    web_search_per_request_usd: Decimal = Decimal("0.01")


_MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-sonnet-4-6": ModelPricing(
        input_per_mtok_usd=Decimal("3"),
        output_per_mtok_usd=Decimal("15"),
        cache_read_per_mtok_usd=Decimal("0.30"),
        cache_creation_per_mtok_usd=Decimal("3.75"),
    ),
    "claude-sonnet-4-5": ModelPricing(
        input_per_mtok_usd=Decimal("3"),
        output_per_mtok_usd=Decimal("15"),
        cache_read_per_mtok_usd=Decimal("0.30"),
        cache_creation_per_mtok_usd=Decimal("3.75"),
    ),
    "claude-haiku-4-5": ModelPricing(
        input_per_mtok_usd=Decimal("1"),
        output_per_mtok_usd=Decimal("5"),
        cache_read_per_mtok_usd=Decimal("0.10"),
        cache_creation_per_mtok_usd=Decimal("1.25"),
    ),
}


def resolve_pricing(model: str) -> ModelPricing | None:
    normalized = model.strip().lower()
    if normalized in _MODEL_PRICING:
        return _MODEL_PRICING[normalized]
    for known_model, pricing in _MODEL_PRICING.items():
        if normalized.startswith(known_model):
            return pricing
    return None
