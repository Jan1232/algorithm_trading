from __future__ import annotations

import itertools
import logging
import time
from typing import Optional

from bot.core.costs import CostModel
from bot.models import (
    ClosedTrade,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
)

logger = logging.getLogger(__name__)


class PaperBroker:
    """
    Virtual broker with adverse slippage on fills.
    Stop-limit: waits for trigger; fills at slipped stop (never better than trigger).
    """

    def __init__(self, costs: Optional[CostModel] = None) -> None:
        self.costs = costs or CostModel()
        self._id_seq = itertools.count(1)
        self._orders: dict[str, Order] = {}
        self._positions: dict[tuple[str, int], Position] = {}
        self._last_price: dict[str, float] = {}
        self.closed_trades: list[ClosedTrade] = []
        self.equity = 0.0

    def _new_id(self) -> str:
        return f"paper-{next(self._id_seq)}"

    def mark_price(self, symbol: str, price: float) -> None:
        self._last_price[symbol] = price
        self._try_fill_stops(symbol, price)
        for pos in list(self._positions.values()):
            if pos.symbol != symbol:
                continue
            if pos.side == Side.LONG:
                pos.unrealized_pnl = (price - pos.entry_price) * pos.qty
            elif pos.side == Side.SHORT:
                pos.unrealized_pnl = (pos.entry_price - price) * pos.qty

    def place_order(self, order: Order) -> Order:
        order.order_id = order.order_id or self._new_id()
        price = self._last_price.get(order.symbol)
        if order.order_type == OrderType.MARKET:
            if price is None:
                order.status = OrderStatus.REJECTED
                return order
            buy = order.side == Side.LONG and not order.reduce_only
            if order.reduce_only:
                # closing: long sells, short buys
                buy = order.side == Side.SHORT
            fill = self.costs.slip_price(price, side_buy=buy)
            self._fill(order, fill)
            return order

        if order.order_type in (OrderType.STOP_LIMIT, OrderType.STOP_MARKET):
            order.status = OrderStatus.WORKING
            self._orders[order.order_id] = order
            # Do NOT fill immediately even if price already through —
            # wait for next mark (honest trigger). If already through on place,
            # mark once more with current price to allow trigger this tick.
            if price is not None and order.stop_price is not None:
                self._maybe_trigger(order, price)
            return order

        order.status = OrderStatus.REJECTED
        return order

    def cancel_order(self, symbol: str, order_id: str) -> None:
        order = self._orders.pop(order_id, None)
        if order and order.symbol == symbol:
            order.status = OrderStatus.CANCELLED

    def cancel_all(self, symbol: Optional[str] = None) -> None:
        for oid, order in list(self._orders.items()):
            if symbol is None or order.symbol == symbol:
                order.status = OrderStatus.CANCELLED
                self._orders.pop(oid, None)

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_position(self, symbol: str, tf_min: int) -> Optional[Position]:
        return self._positions.get((symbol, tf_min))

    def restore_position(self, pos: Position) -> None:
        self._positions[(pos.symbol, pos.tf_min)] = pos

    def _try_fill_stops(self, symbol: str, price: float) -> None:
        for order in list(self._orders.values()):
            if order.symbol == symbol and order.status == OrderStatus.WORKING:
                self._maybe_trigger(order, price)

    def _maybe_trigger(self, order: Order, price: float) -> None:
        if order.stop_price is None:
            return
        triggered = False
        if order.side == Side.LONG and price >= order.stop_price:
            triggered = True
        elif order.side == Side.SHORT and price <= order.stop_price:
            triggered = True
        if not triggered:
            return
        # Fill at slipped stop (adverse), never at a better limit than reality
        base = order.stop_price
        buy = order.side == Side.LONG and not order.reduce_only
        if order.reduce_only:
            buy = order.side == Side.SHORT
        fill_price = self.costs.slip_price(base, side_buy=buy)
        self._fill(order, fill_price)
        self._orders.pop(order.order_id, None)

    def _fill(self, order: Order, price: float) -> None:
        order.status = OrderStatus.FILLED
        tf = order.tf_min if order.tf_min is not None else 0
        key = (order.symbol, tf)
        now = int(time.time() * 1000)

        if order.reduce_only:
            pos = self._positions.get(key)
            if pos is None:
                return
            exit_price = price
            hold_h = 0.0
            if pos.opened_ts_ms:
                hold_h = max(0.0, (now - pos.opened_ts_ms) / 3_600_000.0)
            fees, slip, funding = self.costs.round_trip_costs_usd(
                pos.qty, pos.entry_price, exit_price, hold_hours=hold_h
            )
            if pos.side == Side.LONG:
                gross = (exit_price - pos.entry_price) * pos.qty
            else:
                gross = (pos.entry_price - exit_price) * pos.qty
            pnl = gross - fees - slip - funding
            trade = ClosedTrade(
                symbol=pos.symbol,
                side=pos.side,
                tf_min=pos.tf_min,
                qty=pos.qty,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                pnl=pnl,
                opened_ts_ms=pos.opened_ts_ms,
                closed_ts_ms=now,
                fees_usd=fees,
                slippage_usd=slip,
                funding_usd=funding,
            )
            self.closed_trades.append(trade)
            self.equity += pnl
            del self._positions[key]
            logger.debug(
                "paper close %s tf=%s pnl=%.4f costs=%.4f",
                pos.symbol,
                pos.tf_min,
                pnl,
                fees + slip + funding,
            )
            return

        existing = self._positions.get(key)
        if existing and existing.side != order.side:
            close = Order(
                order_id=self._new_id(),
                symbol=order.symbol,
                side=existing.side,
                order_type=OrderType.MARKET,
                qty=existing.qty,
                reduce_only=True,
                tf_min=tf,
            )
            self._fill(close, price)

        self._positions[key] = Position(
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            entry_price=price,
            tf_min=tf,
            stop_price=order.stop_price or price,
            opened_ts_ms=now,
        )
        logger.debug("paper open %s %s qty=%.6f @ %.4f", order.side, order.symbol, order.qty, price)
