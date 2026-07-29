"""Regression: exchange SL flat must not spam reduce-only / kill-switch."""
from __future__ import annotations

from bot.core.costs import CostModel
from bot.exchange.bybit_client import BybitClient
from bot.models import Order, OrderStatus, OrderType, Position, Side


def test_is_already_flat_error() -> None:
    assert BybitClient._is_already_flat_error(
        "current position is zero, cannot fix reduce-only order qty (ErrCode: 110017)"
    )
    assert BybitClient._is_already_flat_error("zero position")
    assert not BybitClient._is_already_flat_error("insufficient balance")


def test_finalize_reduce_drops_local() -> None:
    client = BybitClient("k", "s", costs=CostModel())
    client.mark_price("SOLUSDT", 74.0)
    client._positions[("SOLUSDT", 15)] = Position(
        symbol="SOLUSDT",
        side=Side.LONG,
        qty=1.3,
        entry_price=74.24,
        tf_min=15,
        stop_price=73.5,
        opened_ts_ms=1,
    )
    order = Order(
        order_id="x",
        symbol="SOLUSDT",
        side=Side.LONG,
        order_type=OrderType.MARKET,
        qty=1.3,
        reduce_only=True,
        tf_min=15,
    )
    client._finalize_reduce(order)
    assert client.get_positions() == []
    assert len(client.closed_trades) == 1
    assert client.closed_trades[0].symbol == "SOLUSDT"


def test_reconcile_exchange_flats_books_when_sync_clears() -> None:
    client = BybitClient("k", "s", costs=CostModel())
    client.mark_price("SOLUSDT", 73.4)
    client._positions[("SOLUSDT", 15)] = Position(
        symbol="SOLUSDT",
        side=Side.LONG,
        qty=1.3,
        entry_price=74.24,
        tf_min=15,
        stop_price=73.5,
        opened_ts_ms=1,
    )

    # Simulate REST sync that finds no open position
    def fake_sync(symbols: list[str]) -> list[Position]:
        for k in list(client._positions):
            if k[0] in symbols:
                client._positions.pop(k, None)
        return []

    client.sync_positions_from_exchange = fake_sync  # type: ignore[method-assign]
    closed = client.reconcile_exchange_flats(["SOLUSDT"])
    assert len(closed) == 1
    assert closed[0].symbol == "SOLUSDT"
    assert client.get_positions() == []
