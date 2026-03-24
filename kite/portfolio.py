from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import Settings, get_settings
from kite.coerce import _coerce_float, _coerce_int
from kite.price import _extract_holdings_payload
from models import Holding, MFHolding, MFSnapshot, PortfolioSnapshot
from providers.kite import KiteMCPClient
from providers.mcp_client import ToolExecutionError
from rebalance import PASSIVE_INSTRUMENTS

logger = logging.getLogger(__name__)


def _holding_market_value(item: dict[str, Any]) -> float:
    current_value = _coerce_float(item.get("current_value"))
    if current_value:
        return current_value
    return _coerce_float(item.get("quantity")) * _coerce_float(item.get("last_price"))


def _extract_available_cash(raw_margins: Any) -> float:
    if isinstance(raw_margins, int | float):
        return float(raw_margins)
    if not isinstance(raw_margins, dict):
        return 0.0
    for path in (
        ("equity", "available", "cash"),
        ("equity", "available", "live_balance"),
        ("equity", "available", "opening_balance"),
        ("available", "cash"),
        ("net",),
    ):
        node: Any = raw_margins
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if node is not None:
            return _coerce_float(node)
    return 0.0


def _extract_mf_value(raw_mf_holdings: Any) -> float:
    total = 0.0
    for item in _extract_holdings_payload(raw_mf_holdings):
        current_value = _coerce_float(item.get("current_value"))
        if current_value == 0.0:
            current_value = _coerce_float(item.get("last_price")) * _coerce_float(item.get("quantity"))
        total += current_value
    return total


def _parse_target_weights_from_rules(rules_path: Path) -> dict[str, float]:
    if not rules_path.exists():
        return {}
    weights: dict[str, float] = {}
    for raw_line in rules_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if "%" not in line or ":" not in line:
            continue
        lhs, rhs = line.split(":", maxsplit=1)
        symbol = lhs.translate(str.maketrans("", "", "- *`")).strip().upper()
        pct_text = rhs.split("%", maxsplit=1)[0].strip()
        try:
            weights[symbol] = float(pct_text)
        except ValueError:
            continue
    return weights


def _assign_target_weights(raw_holdings: list[dict[str, Any]], explicit_targets: dict[str, float]) -> dict[str, float]:
    eligible_symbols = [
        str(item.get("tradingsymbol", "")).upper()
        for item in raw_holdings
        if str(item.get("tradingsymbol", "")).upper() not in PASSIVE_INSTRUMENTS
    ]
    default_weight = round(100.0 / len(eligible_symbols), 4) if eligible_symbols else 0.0
    targets: dict[str, float] = {}
    for item in raw_holdings:
        symbol = str(item.get("tradingsymbol", "")).upper()
        targets[symbol] = 0.0 if symbol in PASSIVE_INSTRUMENTS else explicit_targets.get(symbol, default_weight)
    return targets


def _normalize_holding(item: dict[str, Any], total_portfolio_value: float, targets: dict[str, float]) -> Holding:
    quantity = _coerce_int(item.get("quantity"))
    average_price = _coerce_float(item.get("average_price"))
    last_price = _coerce_float(item.get("last_price"))
    current_value = _coerce_float(item.get("current_value"), default=quantity * last_price)
    pnl = _coerce_float(item.get("pnl"), default=(last_price - average_price) * quantity)
    pnl_pct = _coerce_float(
        item.get("pnl_percentage"),
        default=((pnl / (average_price * quantity)) * 100.0 if average_price and quantity else 0.0),
    )
    symbol = str(item.get("tradingsymbol", "")).upper()
    return Holding(
        tradingsymbol=symbol,
        exchange=str(item.get("exchange", "NSE")).upper(),
        quantity=quantity,
        average_price=average_price,
        last_price=last_price,
        current_value=current_value,
        current_weight_pct=(current_value / total_portfolio_value * 100.0) if total_portfolio_value else 0.0,
        target_weight_pct=targets.get(symbol, 0.0),
        pnl=pnl,
        pnl_pct=pnl_pct,
        instrument_token=_coerce_int(item.get("instrument_token")),
    )


def _normalize_mf_holding(item: dict[str, Any]) -> MFHolding:
    quantity = _coerce_float(item.get("quantity"))
    average_price = _coerce_float(item.get("average_price"))
    last_price = _coerce_float(item.get("last_price"))
    current_value = _coerce_float(item.get("current_value"), default=quantity * last_price)
    pnl = _coerce_float(item.get("pnl"), default=current_value - (average_price * quantity))
    base_value = average_price * quantity
    pnl_pct = _coerce_float(item.get("pnl_percentage"), default=((pnl / base_value) * 100.0 if base_value else 0.0))
    return MFHolding(
        tradingsymbol=str(item.get("tradingsymbol", item.get("symbol", ""))).upper(),
        fund=str(item.get("fund", item.get("scheme_name", item.get("name", "")))),
        folio=str(item.get("folio", "")),
        quantity=quantity,
        average_price=average_price,
        last_price=last_price,
        current_value=current_value,
        pnl=pnl,
        pnl_pct=pnl_pct,
        scheme_type=str(item.get("scheme_type", item.get("type", ""))),
        plan=str(item.get("plan", "")),
    )


async def kite_get_portfolio(kite_client: KiteMCPClient, settings: Settings | None = None) -> PortfolioSnapshot:
    settings = settings or get_settings()
    explicit_targets = _parse_target_weights_from_rules(Path("skills") / "portfolio_rules.md")
    try:
        raw_holdings, raw_margins, raw_mf_holdings = await asyncio.gather(
            kite_client.call_tool("get_holdings"),
            kite_client.call_tool("get_margins"),
            kite_client.call_tool("get_mf_holdings"),
        )
    except Exception as exc:
        raise ToolExecutionError(
            "Kite MCP not connected or session expired. Start the MCP command configured for Artha, complete Zerodha login if required, then retry."
        ) from exc
    holdings_payload = _extract_holdings_payload(raw_holdings)
    total_equity_value = sum(_holding_market_value(item) for item in holdings_payload)
    available_cash = _extract_available_cash(raw_margins)
    total_value = total_equity_value + available_cash
    target_weights = _assign_target_weights(holdings_payload, explicit_targets)
    holdings = [
        _normalize_holding(item=item, total_portfolio_value=total_value, targets=target_weights)
        for item in holdings_payload
    ]
    mf_total_value = _extract_mf_value(raw_mf_holdings)
    if mf_total_value:
        logger.info("MF holdings value excluded from rebalancing portfolio total: %.2f", mf_total_value)
    return PortfolioSnapshot(
        fetched_at=datetime.now(UTC), total_value=total_value, available_cash=available_cash, holdings=holdings
    )


async def kite_get_mf_snapshot(kite_client: KiteMCPClient, settings: Settings | None = None) -> MFSnapshot:
    del settings
    try:
        raw_mf_holdings = await kite_client.call_tool("get_mf_holdings")
    except Exception as exc:
        raise ToolExecutionError("Failed to fetch MF holdings from Kite MCP.") from exc
    holdings = [_normalize_mf_holding(item) for item in _extract_holdings_payload(raw_mf_holdings)]
    return MFSnapshot(
        fetched_at=datetime.now(UTC), total_value=sum(holding.current_value for holding in holdings), holdings=holdings
    )
