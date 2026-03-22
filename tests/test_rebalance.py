from models import Holding
from rebalance import calculate_drift, calculate_rebalancing_actions


def make_holding(symbol: str, current: float, target: float, last_price: float = 100.0) -> Holding:
    return Holding(
        tradingsymbol=symbol,
        exchange="NSE",
        quantity=10,
        average_price=90.0,
        last_price=last_price,
        current_value=1000.0,
        current_weight_pct=current,
        target_weight_pct=target,
        pnl=100.0,
        pnl_pct=11.11,
        instrument_token=123,
    )


def test_drift_calculation() -> None:
    holdings = [make_holding("HDFCBANK", 10.0, 8.0), make_holding("KPITTECH", 4.0, 6.0)]
    assert calculate_drift(holdings) == {"HDFCBANK": 2.0, "KPITTECH": -2.0}


def test_rebalancing_actions_buy() -> None:
    actions = calculate_rebalancing_actions([make_holding("KPITTECH", 4.0, 8.0)], total_value=100_000, available_cash=0)
    assert actions[0].action == "BUY"


def test_rebalancing_actions_sell() -> None:
    actions = calculate_rebalancing_actions([make_holding("BSE", 14.0, 8.0)], total_value=100_000, available_cash=0)
    assert actions[0].action == "SELL"


def test_rebalancing_hold() -> None:
    actions = calculate_rebalancing_actions([make_holding("HDFCBANK", 7.0, 8.5)], total_value=100_000, available_cash=0)
    assert actions[0].action == "HOLD"


def test_urgency_levels() -> None:
    low = calculate_rebalancing_actions([make_holding("A", 5.5, 8.0)], total_value=100_000, available_cash=0)[0]
    medium = calculate_rebalancing_actions([make_holding("B", 11.5, 8.0)], total_value=100_000, available_cash=0)[0]
    high = calculate_rebalancing_actions([make_holding("C", 14.0, 8.0)], total_value=100_000, available_cash=0)[0]
    assert low.urgency == "LOW"
    assert medium.urgency == "MEDIUM"
    assert high.urgency == "HIGH"


def test_etf_excluded() -> None:
    actions = calculate_rebalancing_actions(
        [make_holding("LIQUIDBEES", 12.0, 0.0), make_holding("HDFCBANK", 7.0, 8.0)],
        total_value=100_000,
        available_cash=0,
    )
    assert len(actions) == 1
    assert actions[0].tradingsymbol == "HDFCBANK"
