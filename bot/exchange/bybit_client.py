from __future__ import annotations

import itertools
import logging
import time
from typing import Callable, Optional

from bot.core.costs import CostModel
from bot.exchange.base import side_to_bybit
from bot.exchange.instruments import InstrumentRegistry
from bot.models import ClosedTrade, Order, OrderStatus, OrderType, Position, Side, Tick

logger = logging.getLogger(__name__)


class BybitClient:
    """
    Bybit v5 adapter via pybit (demo/live).

    - REST for orders / positions / wallet / trading-stop (SL)
    - Tracks pending conditional orders and reconciles fills via get_open_orders
    - Local mirror is one logical position per (symbol, tf_min); manager enforces
      one net position per symbol to match Bybit linear one-way mode
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        demo: bool = True,
        testnet: bool = False,
        category: str = "linear",
        on_tick: Optional[Callable[[Tick], None]] = None,
        instruments: Optional[InstrumentRegistry] = None,
        costs: Optional[CostModel] = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.demo = demo
        self.testnet = testnet
        self.category = category
        self.on_tick = on_tick
        self.instruments = instruments or InstrumentRegistry()
        self.costs = costs or CostModel()
        self.closed_trades: list[ClosedTrade] = []
        self.equity = 0.0
        self._id_seq = itertools.count(1)
        self._last_price: dict[str, float] = {}
        self._positions: dict[tuple[str, int], Position] = {}
        self._pending: dict[str, Order] = {}  # orderId -> Order
        self._http = None
        self._ws = None
        self._running = False
        self.position_idx = 0  # one-way mode

    def connect(self, symbols: Optional[list[str]] = None) -> None:
        from pybit.unified_trading import HTTP, WebSocket

        if self.demo and self.testnet:
            raise ValueError("Do not combine demo=True with testnet=True")

        self._http = HTTP(
            testnet=self.testnet,
            demo=self.demo,
            api_key=self.api_key or None,
            api_secret=self.api_secret or None,
        )
        # Optional private WS kept for compatibility; ticks preferably via PublicTradeFeed
        self._ws = WebSocket(
            testnet=self.testnet,
            channel_type="linear",
        )
        self.instruments.load_from_bybit(self._http, category=self.category, symbols=symbols)
        logger.info(
            "Bybit connected demo=%s testnet=%s category=%s",
            self.demo,
            self.testnet,
            self.category,
        )

    def get_wallet_usdt(self) -> Optional[float]:
        """UNIFIED wallet USDT equity (Bybit v5 get_wallet_balance)."""
        if self._http is None:
            return None
        try:
            resp = self._http.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            rows = ((resp.get("result") or {}).get("list")) or []
            if not rows:
                return None
            # Prefer coin equity; fall back to account totals
            coins = rows[0].get("coin") or []
            for c in coins:
                if (c.get("coin") or "").upper() == "USDT":
                    for key in ("equity", "walletBalance", "usdValue"):
                        val = c.get(key)
                        if val not in (None, ""):
                            return float(val)
            for key in ("totalAvailableBalance", "totalWalletBalance", "totalEquity"):
                val = rows[0].get(key)
                if val not in (None, ""):
                    return float(val)
        except Exception as exc:
            safe = str(exc).replace("\u2192", "->").encode("ascii", "replace").decode("ascii")
            logger.error("get_wallet_usdt failed: %s", safe)
        return None

    def sync_positions_from_exchange(self, symbols: list[str]) -> list[Position]:
        """Pull open linear positions via REST and mirror locally."""
        if self._http is None:
            raise RuntimeError("call connect() first")
        synced: list[Position] = []
        try:
            for symbol in symbols:
                resp = self._http.get_positions(category=self.category, symbol=symbol)
                rows = ((resp.get("result") or {}).get("list")) or []
                found = False
                for row in rows:
                    size = float(row.get("size") or 0)
                    if size <= 0:
                        continue
                    found = True
                    side_raw = (row.get("side") or "").lower()
                    side = Side.LONG if side_raw == "buy" else Side.SHORT
                    entry = float(row.get("avgPrice") or row.get("entryPrice") or 0)
                    stop = float(row.get("stopLoss") or 0) or entry
                    existing_key = next(
                        (k for k in self._positions if k[0] == symbol),
                        (symbol, 0),
                    )
                    # Drop other TF keys for this symbol (Bybit = one net)
                    for k in list(self._positions):
                        if k[0] == symbol and k != existing_key:
                            self._positions.pop(k, None)
                    prev = self._positions.get(existing_key)
                    pos = Position(
                        symbol=symbol,
                        side=side,
                        qty=size,
                        entry_price=entry,
                        tf_min=existing_key[1],
                        stop_price=stop or (prev.stop_price if prev else entry),
                        opened_ts_ms=(prev.opened_ts_ms if prev and prev.opened_ts_ms else int(time.time() * 1000)),
                    )
                    self._positions[existing_key] = pos
                    synced.append(pos)
                    logger.warning(
                        "synced exchange position %s %s qty=%s @ %s stop=%s (tf_min=%s)",
                        side.value,
                        symbol,
                        size,
                        entry,
                        stop,
                        existing_key[1],
                    )
                if not found:
                    for k in list(self._positions):
                        if k[0] == symbol:
                            self._positions.pop(k, None)
        except Exception:
            logger.exception("sync_positions_from_exchange failed")
        return synced

    def set_stop_loss(self, symbol: str, stop_price: float) -> bool:
        """Bybit v5 POST /v5/position/trading-stop (Full + Market SL)."""
        if self._http is None or stop_price <= 0:
            return False
        spec = self.instruments.get(symbol)
        sl = str(spec.round_price(stop_price))
        try:
            self._http.set_trading_stop(
                category=self.category,
                symbol=symbol,
                stopLoss=sl,
                tpslMode="Full",
                positionIdx=self.position_idx,
                slTriggerBy="LastPrice",
                slOrderType="Market",
            )
            logger.info("bybit set_trading_stop %s stopLoss=%s", symbol, sl)
            return True
        except Exception as exc:
            msg = str(exc)
            # 34040 = not modified (same SL already set)
            if "34040" in msg or "not modified" in msg.lower():
                return True
            # 10001 = already flat (exchange SL/TP already closed the position)
            if "10001" in msg or "zero position" in msg.lower():
                logger.warning(
                    "set_trading_stop skipped %s — position already flat on exchange",
                    symbol,
                )
                return False
            safe = msg.replace("\u2192", "->").encode("ascii", "replace").decode("ascii")
            logger.error("set_trading_stop failed %s: %s", symbol, safe)
            return False

    def has_pending(self, symbol: str) -> bool:
        return any(o.symbol == symbol for o in self._pending.values())

    def reconcile_pending(self) -> list[Position]:
        """
        Poll open orders: if a pending STOP_* left the book, adopt exchange position.
        Returns newly adopted local positions (caller should persist open_trade + SL).
        """
        if self._http is None or not self._pending:
            return []
        adopted: list[Position] = []
        for oid, order in list(self._pending.items()):
            try:
                resp = self._http.get_open_orders(
                    category=self.category,
                    symbol=order.symbol,
                    orderId=oid,
                )
                rows = ((resp.get("result") or {}).get("list")) or []
                if rows:
                    continue  # still working
                # Order gone from open book — check history status
                hist = self._http.get_order_history(
                    category=self.category,
                    symbol=order.symbol,
                    orderId=oid,
                    limit=1,
                )
                hrows = ((hist.get("result") or {}).get("list")) or []
                status = (hrows[0].get("orderStatus") if hrows else "") or ""
                status_l = status.lower()
                if status_l in ("cancelled", "rejected", "deactivated"):
                    logger.info("pending order %s %s ended as %s", oid, order.symbol, status)
                    self._pending.pop(oid, None)
                    continue
                # Filled / triggered / unknown-but-gone → sync position
                self._pending.pop(oid, None)
                self.sync_positions_from_exchange([order.symbol])
                pos = self.get_position(order.symbol, order.tf_min or 0)
                if pos is None:
                    # attach under order.tf_min if exchange has net position
                    for p in self.get_positions():
                        if p.symbol == order.symbol:
                            old_key = (p.symbol, p.tf_min)
                            self._positions.pop(old_key, None)
                            p.tf_min = int(order.tf_min or 0)
                            p.opened_ts_ms = p.opened_ts_ms or int(time.time() * 1000)
                            if order.stop_price:
                                # keep protect separate; entry trigger was stop_price
                                pass
                            self._positions[(p.symbol, p.tf_min)] = p
                            pos = p
                            break
                if pos is not None:
                    logger.info(
                        "reconciled fill %s %s qty=%s tf=%s status=%s",
                        pos.side.value,
                        pos.symbol,
                        pos.qty,
                        pos.tf_min,
                        status or "assumed_filled",
                    )
                    adopted.append(pos)
            except Exception as exc:
                safe = str(exc).replace("\u2192", "->").encode("ascii", "replace").decode("ascii")
                logger.error("reconcile_pending failed %s: %s", oid, safe)
        return adopted

    def subscribe_trades(self, symbols: list[str]) -> None:
        """Legacy path; prefer PublicTradeFeed in engine for reconnect."""
        if self._ws is None:
            raise RuntimeError("call connect() first")

        def _handler(message: dict) -> None:
            try:
                data = message.get("data")
                if not data:
                    return
                rows = data if isinstance(data, list) else [data]
                for row in rows:
                    symbol = row.get("s") or row.get("symbol")
                    price = float(row.get("p") or row.get("price"))
                    size = float(row.get("v") or row.get("size") or 0)
                    ts = int(row.get("T") or row.get("ts") or time.time() * 1000)
                    tick = Tick(symbol=symbol, price=price, size=size, ts_ms=ts)
                    self._last_price[symbol] = price
                    if self.on_tick:
                        self.on_tick(tick)
            except Exception:
                logger.exception("trade handler error")

        for symbol in symbols:
            self._ws.trade_stream(symbol=symbol, callback=_handler)
        self._running = True

    def close(self) -> None:
        self._running = False
        self._ws = None

    def mark_price(self, symbol: str, price: float) -> None:
        self._last_price[symbol] = price

    def place_order(self, order: Order) -> Order:
        if self._http is None:
            raise RuntimeError("call connect() first")

        spec = self.instruments.get(order.symbol)
        qty = spec.round_qty(float(order.qty))
        if qty <= 0:
            order.status = OrderStatus.REJECTED
            return order
        order.qty = qty

        api_side = order.side
        if order.reduce_only:
            api_side = Side.SHORT if order.side == Side.LONG else Side.LONG

        params: dict = {
            "category": self.category,
            "symbol": order.symbol,
            "side": side_to_bybit(api_side),
            "qty": str(qty),
        }
        if order.reduce_only:
            params["reduceOnly"] = True

        if order.order_type == OrderType.MARKET:
            params["orderType"] = "Market"
        elif order.order_type == OrderType.STOP_MARKET:
            params["orderType"] = "Market"
            params["triggerPrice"] = str(spec.round_price(float(order.stop_price or 0)))
            params["triggerDirection"] = 1 if order.side == Side.LONG else 2
        elif order.order_type == OrderType.STOP_LIMIT:
            params["orderType"] = "Limit"
            px = spec.round_price(float(order.price or order.stop_price or 0))
            trig = spec.round_price(float(order.stop_price or px))
            params["price"] = str(px)
            params["triggerPrice"] = str(trig)
            params["triggerDirection"] = 1 if order.side == Side.LONG else 2
        else:
            order.status = OrderStatus.REJECTED
            return order

        try:
            resp = self._http.place_order(**params)
            result = resp.get("result") or {}
            order.order_id = str(result.get("orderId") or self._new_id())
            order.status = (
                OrderStatus.WORKING if order.order_type != OrderType.MARKET else OrderStatus.FILLED
            )
            if order.order_type != OrderType.MARKET and not order.reduce_only:
                self._pending[order.order_id] = order
            if order.order_type == OrderType.MARKET and not order.reduce_only and order.tf_min is not None:
                # Prefer exchange avg after brief sync
                px = self._last_price.get(order.symbol) or float(order.price or 0)
                self.sync_positions_from_exchange([order.symbol])
                synced = next((p for p in self.get_positions() if p.symbol == order.symbol), None)
                if synced is not None:
                    # re-key to strategy tf
                    for k in list(self._positions):
                        if k[0] == order.symbol:
                            self._positions.pop(k, None)
                    synced.tf_min = order.tf_min
                    synced.opened_ts_ms = int(time.time() * 1000)
                    if order.stop_price:
                        synced.stop_price = float(order.stop_price)
                    self._positions[(order.symbol, order.tf_min)] = synced
                else:
                    self._positions[(order.symbol, order.tf_min)] = Position(
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        entry_price=px,
                        tf_min=order.tf_min,
                        stop_price=order.stop_price or px,
                        opened_ts_ms=int(time.time() * 1000),
                    )
            if order.reduce_only:
                self._finalize_reduce(order)
            logger.info(
                "bybit order ok %s %s qty=%s type=%s reduce=%s id=%s",
                api_side.value,
                order.symbol,
                order.qty,
                order.order_type.value,
                order.reduce_only,
                order.order_id,
            )
            return order
        except Exception as exc:
            safe = str(exc).replace("\u2192", "->").encode("ascii", "replace").decode("ascii")
            # Exchange already flat (SL/TP/liq) — drop local mirror once, no retry spam
            if order.reduce_only and self._is_already_flat_error(safe):
                logger.warning(
                    "reduce-only %s already flat on exchange — finalizing locally",
                    order.symbol,
                )
                self._finalize_reduce(order)
                order.status = OrderStatus.FILLED
                order.order_id = order.order_id or f"ghost-{self._new_id()}"
                return order
            logger.error("place_order failed: %s", safe)
            order.status = OrderStatus.REJECTED
            return order

    @staticmethod
    def _is_already_flat_error(msg: str) -> bool:
        m = msg.lower()
        return (
            "110017" in msg
            or "position is zero" in m
            or "zero position" in m
            or "cannot fix reduce-only" in m
        )

    def reconcile_exchange_flats(self, symbols: list[str]) -> list[ClosedTrade]:
        """
        Sync REST positions; if local has a symbol that is flat on Bybit
        (exchange SL/TP hit), finalize as a closed trade.
        """
        if not symbols:
            return []
        before = {p.symbol: p for p in self.get_positions()}
        if not before:
            return []
        watch = [s for s in symbols if s in before]
        if not watch:
            return []
        self.sync_positions_from_exchange(watch)
        still = {p.symbol for p in self.get_positions()}
        closed: list[ClosedTrade] = []
        for symbol, pos in before.items():
            if symbol in still:
                continue
            # Put back temporarily so _finalize_reduce can book PnL
            key = (pos.symbol, pos.tf_min)
            self._positions[key] = pos
            n_before = len(self.closed_trades)
            fake = Order(
                order_id=f"exchange-flat-{symbol}",
                symbol=symbol,
                side=pos.side,
                order_type=OrderType.MARKET,
                qty=pos.qty,
                reduce_only=True,
                tf_min=pos.tf_min,
                client_tag="exchange_sl",
            )
            self._finalize_reduce(fake)
            if len(self.closed_trades) > n_before:
                closed.append(self.closed_trades[-1])
                logger.info(
                    "reconciled exchange flat %s tf=%s (likely SL/TP on Bybit)",
                    symbol,
                    pos.tf_min,
                )
        return closed

    def _finalize_reduce(self, order: Order) -> None:
        key = None
        pos = None
        if order.tf_min is not None:
            key = (order.symbol, order.tf_min)
            pos = self._positions.get(key)
        if pos is None:
            for k, p in list(self._positions.items()):
                if k[0] == order.symbol:
                    key, pos = k, p
                    break
        if pos is None or key is None:
            return

        now = int(time.time() * 1000)
        exit_price = self._last_price.get(order.symbol) or pos.entry_price
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
        # cancel leftover pending entries for symbol
        for oid, o in list(self._pending.items()):
            if o.symbol == order.symbol:
                self._pending.pop(oid, None)
                self.cancel_order(order.symbol, oid)
        logger.info(
            "bybit close %s tf=%s pnl=%.4f costs=%.4f",
            pos.symbol,
            pos.tf_min,
            pnl,
            fees + slip + funding,
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        if self._http is None:
            return
        try:
            self._http.cancel_order(category=self.category, symbol=symbol, orderId=order_id)
        except Exception:
            logger.exception("cancel_order failed")

    def cancel_all(self, symbol: Optional[str] = None) -> None:
        if self._http is None:
            return
        try:
            if symbol:
                self._http.cancel_all_orders(category=self.category, symbol=symbol)
            else:
                self._http.cancel_all_orders(category=self.category, settleCoin="USDT")
            self._pending.clear()
        except Exception as exc:
            safe = str(exc).replace("\u2192", "->").encode("ascii", "replace").decode("ascii")
            logger.error("cancel_all failed: %s", safe)

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_position(self, symbol: str, tf_min: int) -> Optional[Position]:
        return self._positions.get((symbol, tf_min))

    def get_position_any(self, symbol: str) -> Optional[Position]:
        for (sym, _), pos in self._positions.items():
            if sym == symbol:
                return pos
        return None

    def rematch_tf_from_open_trades(self, open_trades: list[dict]) -> None:
        used_ids: set[int] = set()
        for key, pos in list(self._positions.items()):
            candidates = [
                r
                for r in open_trades
                if r.get("symbol") == pos.symbol
                and str(r.get("side")) == pos.side.value
                and int(r.get("id") or 0) not in used_ids
            ]
            if not candidates:
                continue
            candidates.sort(
                key=lambda r: (
                    abs(float(r.get("qty") or 0) - pos.qty),
                    -int(r.get("opened_ts_ms") or 0),
                )
            )
            best = candidates[0]
            tf = int(best["tf_min"])
            trade_id = int(best.get("id") or 0)
            if trade_id:
                used_ids.add(trade_id)
            if tf == pos.tf_min and key == (pos.symbol, tf):
                continue
            self._positions.pop(key, None)
            pos.tf_min = tf
            if best.get("opened_ts_ms"):
                pos.opened_ts_ms = int(best["opened_ts_ms"])
            protect = None
            try:
                import json

                market = json.loads(best.get("market_json") or "{}")
                protect = market.get("protect_stop")
            except Exception:
                protect = None
            if protect:
                pos.stop_price = float(protect)
            self._positions[(pos.symbol, tf)] = pos
            logger.info(
                "rematched exchange position %s %s qty=%s -> tf_min=%s",
                pos.side.value,
                pos.symbol,
                pos.qty,
                tf,
            )

    def _new_id(self) -> str:
        return f"bybit-{next(self._id_seq)}"
