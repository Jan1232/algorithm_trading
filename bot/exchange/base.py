from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from bot.models import Order, Position, Side


@runtime_checkable
class Broker(Protocol):
    def place_order(self, order: Order) -> Order: ...

    def cancel_order(self, symbol: str, order_id: str) -> None: ...

    def cancel_all(self, symbol: Optional[str] = None) -> None: ...

    def get_positions(self) -> list[Position]: ...

    def get_position(self, symbol: str, tf_min: int) -> Optional[Position]: ...

    def mark_price(self, symbol: str, price: float) -> None: ...


def side_to_bybit(side: Side) -> str:
    if side == Side.LONG:
        return "Buy"
    if side == Side.SHORT:
        return "Sell"
    raise ValueError(f"unsupported side {side}")
