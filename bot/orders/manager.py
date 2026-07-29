from __future__ import annotations

import logging
import time
import uuid
from typing import Callable, Optional, Protocol

from bot.core.allocator import Allocation, allocate_deposit
from bot.core.risk import (
    TfDrawdownTracker,
    echelon2_allows_new,
    stop_hit,
    trailing_stop_price,
)
from bot.core.validator import StabilityValidator
from bot.exchange.instruments import InstrumentRegistry
from bot.models import (
    Bar,
    ClosedTrade,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Signal,
    SignalKind,
    VoteResult,
)
from bot.orders.killswitch import KillSwitch
from bot.storage.db import TradeStore

logger = logging.getLogger(__name__)


class _Broker(Protocol):
    def place_order(self, order: Order) -> Order: ...

    def cancel_all(self, symbol: Optional[str] = None) -> None: ...

    def get_positions(self) -> list[Position]: ...

    def get_position(self, symbol: str, tf_min: int) -> Optional[Position]: ...


class OrderManager:
    """
    Entries: stop-limit breakout. Exits depend on exit_mode:
      - hybrid: rule3 FLAT + trailing stop (echelon 1)
      - book_close: Chebotarev §3 close-turn + trailing stop (no rule3 flat exit)

    For Bybit demo/live: one net position per symbol; exchange SL via set_trading_stop.
    """

    def __init__(
        self,
        broker: _Broker,
        *,
        deposit: float,
        max_drawdown_usd: float,
        tf_risk_pct: float,
        kill_switch: KillSwitch,
        validator: Optional[StabilityValidator] = None,
        tracker: Optional[TfDrawdownTracker] = None,
        store: Optional[TradeStore] = None,
        exit_mode: str = "hybrid",
        instruments: Optional[InstrumentRegistry] = None,
        on_kill: Optional[Callable[[str], None]] = None,
        mode: str = "paper",
        policy_hash: Optional[str] = None,
        one_position_per_symbol: bool = True,
        per_trade_risk_pct: float = 0.02,
        max_leverage_frac: float = 1.0,
        trailing_buffer_frac: float = 0.10,
    ) -> None:
        self.broker = broker
        self.deposit = deposit
        self.max_drawdown_usd = max_drawdown_usd
        self.tf_risk_pct = tf_risk_pct
        self.kill_switch = kill_switch
        self.validator = validator or StabilityValidator()
        self.tracker = tracker or TfDrawdownTracker()
        self.store = store
        self.exit_mode = exit_mode
        self.instruments = instruments or InstrumentRegistry()
        self.on_kill = on_kill
        self.mode = mode
        self.policy_hash = policy_hash
        self.one_position_per_symbol = one_position_per_symbol
        self.per_trade_risk_pct = per_trade_risk_pct
        self.max_leverage_frac = max_leverage_frac
        self.trailing_buffer_frac = trailing_buffer_frac
        self._pending_entries: dict[tuple[str, int], Order] = {}
        self._pending_protect: dict[str, float] = {}  # symbol -> protect SL while stop works
        self._pending_meta: dict[tuple[str, int], dict] = {}  # entry diagnostics for reconcile
        self._last_vote: Optional[VoteResult] = None
        self._signal_ids: dict[tuple[str, int], int] = {}
        self._last_close: dict[tuple[str, int], float] = {}
        self._exchange_sl: dict[str, float] = {}  # last SL pushed to Bybit
        self._closing_symbols: set[str] = set()  # prevent tick spam while closing

    def _check_kill(self) -> None:
        if self.kill_switch.state.halted and self.on_kill:
            self.on_kill(self.kill_switch.state.reason)

    def _push_exchange_sl(self, symbol: str, stop_price: float) -> None:
        setter = getattr(self.broker, "set_stop_loss", None)
        if not callable(setter) or stop_price <= 0:
            return
        prev = self._exchange_sl.get(symbol)
        if prev is not None and abs(prev - stop_price) / max(abs(stop_price), 1e-9) < 1e-6:
            return
        if setter(symbol, stop_price):
            self._exchange_sl[symbol] = stop_price

    def flush_flat_after_sync(self, store: TradeStore) -> None:
        """Close rematched positions whose latest signal is already FLAT."""
        if self.exit_mode != "hybrid" or store is None:
            return
        import sqlite3

        conn = sqlite3.connect(store.db_path)
        conn.row_factory = sqlite3.Row
        for pos in list(self.broker.get_positions()):
            row = conn.execute(
                """
                SELECT kind FROM signals
                WHERE symbol=? AND tf_min=?
                ORDER BY ts_ms DESC LIMIT 1
                """,
                (pos.symbol, pos.tf_min),
            ).fetchone()
            if row is not None and row["kind"] == "flat":
                logger.info(
                    "flush flat-after-sync: closing %s tf=%s (latest signal already FLAT)",
                    pos.symbol,
                    pos.tf_min,
                )
                self._close_position(pos, reason="rule3_flat")
        conn.close()

    def reconcile(self) -> None:
        """Poll pending stop fills, exchange flats (SL/TP), persist + push SL."""
        reconciler = getattr(self.broker, "reconcile_pending", None)
        if callable(reconciler):
            adopted = reconciler()
            for pos in adopted:
                protect = self._pending_protect.pop(pos.symbol, None)
                if protect and (pos.stop_price <= 0 or abs(pos.stop_price - pos.entry_price) < 1e-12):
                    pos.stop_price = protect
                self._persist_adopted_position(pos)
                self._push_exchange_sl(pos.symbol, pos.stop_price)

        # Bybit exchange SL/TP can close without our reduce-only — sync and book
        flattener = getattr(self.broker, "reconcile_exchange_flats", None)
        if callable(flattener):
            symbols = sorted(
                {p.symbol for p in self.broker.get_positions()}
                | set(self._exchange_sl)
                | set(self._pending_protect)
            )
            if symbols:
                before = list(getattr(self.broker, "closed_trades", []))
                flattener(symbols)
                after = list(getattr(self.broker, "closed_trades", []))
                for trade in after[len(before) :]:
                    self._book_closed_trade(trade, reason="exchange_sl")
                    self._exchange_sl.pop(trade.symbol, None)
                    self._pending_protect.pop(trade.symbol, None)
                    self._closing_symbols.discard(trade.symbol)

    def _vote_payload(self, vote: Optional[VoteResult]) -> Optional[dict]:
        if vote is None:
            return None
        return {
            "long": vote.n_long,
            "short": vote.n_short,
            "flat": vote.n_flat,
            "none": vote.n_none,
            "dominant": vote.dominant.value,
            "n_total": vote.n_total,
            "n_voting": vote.n_voting,
            "n_directional": vote.n_directional,
        }

    @staticmethod
    def _slippage_vs_trigger(side: Side, entry_price: float, trigger: float) -> float:
        """Adverse slip vs planned trigger (+ = worse fill)."""
        if trigger <= 0:
            return 0.0
        if side == Side.LONG:
            return float(entry_price) - float(trigger)
        return float(trigger) - float(entry_price)

    def _persist_adopted_position(self, pos: Position) -> None:
        if self.store is None:
            return
        opens = self.store.list_open_trades()
        already = any(
            r["symbol"] == pos.symbol
            and r["side"] == pos.side.value
            and abs(float(r["qty"]) - pos.qty) < 1e-9
            for r in opens
        )
        if already:
            return
        # Close stale open rows for same symbol (one net)
        for r in opens:
            if r["symbol"] == pos.symbol and r.get("id") is not None:
                self.store.close_orphan_open(trade_id=int(r["id"]), reason="replaced_by_fill")
        key = (pos.symbol, pos.tf_min)
        pending = self._pending_entries.get(key)
        meta = self._pending_meta.pop(key, {}) or {}
        trigger = float(meta.get("trigger") or 0.0)
        already = bool(meta.get("already_through", False))
        entry_type = meta.get("entry_type") or (
            "market_through" if already else "stop_limit_filled"
        )
        market = {
            "source": "reconcile_fill",
            "entry_type": entry_type,
            "already_through": already,
            "order_type": "market" if already else "stop_limit",
            "protect_stop": pos.stop_price,
            "mode": self.mode,
            "policy_hash": self.policy_hash,
            "order_id": pending.order_id if pending else meta.get("order_id"),
            "trigger": trigger or meta.get("trigger"),
            "signal_price": meta.get("signal_price"),
            "vote": meta.get("vote") or self._vote_payload(self._last_vote),
            "slippage_vs_trigger": self._slippage_vs_trigger(
                pos.side, pos.entry_price, trigger
            )
            if trigger
            else None,
            "entry_reason_signal": meta.get("entry_reason"),
            "risk_usd": meta.get("risk_usd"),
            "exit_mode": self.exit_mode,
        }
        self.store.open_trade(
            symbol=pos.symbol,
            tf_min=pos.tf_min,
            side=pos.side.value,
            qty=pos.qty,
            entry_price=pos.entry_price,
            entry_reason=meta.get("entry_reason") or "stop_fill_reconcile",
            market=market,
            signal_id=self._signal_ids.get(key),
            opened_ts_ms=pos.opened_ts_ms or int(time.time() * 1000),
            mode=self.mode,
            policy_hash=self.policy_hash,
        )
        logger.info(
            "persisted reconciled fill %s %s tf=%s qty=%s @ %s entry_type=%s slip_vs_trig=%s",
            pos.side.value,
            pos.symbol,
            pos.tf_min,
            pos.qty,
            pos.entry_price,
            entry_type,
            market.get("slippage_vs_trigger"),
        )

    def on_price(self, symbol: str, price: float, prev_bars: dict[int, Bar]) -> None:
        for pos in list(self.broker.get_positions()):
            if pos.symbol != symbol:
                continue
            if symbol in self._closing_symbols:
                continue
            # Soft stop first — if already through, close without pushing exchange SL
            if stop_hit(pos, price):
                self._close_position(pos, reason="trailing_stop")
                continue
            prev = prev_bars.get(pos.tf_min)
            # Synced exchange positions may have tf_min=0 — fall back to shortest bar
            if prev is None and prev_bars:
                prev = prev_bars[min(prev_bars.keys())]
            if prev is not None:
                pos.stop_price = trailing_stop_price(
                    pos.side,
                    prev.low,
                    prev.high,
                    buffer_frac=self.trailing_buffer_frac,
                    bar_range=prev.range,
                )
                self._push_exchange_sl(pos.symbol, pos.stop_price)

    def on_signals(
        self,
        symbol: str,
        price: float,
        signals: list[Signal],
        vote: VoteResult,
        prev_bars: dict[int, Bar],
        signal_ids: Optional[dict[int, int]] = None,
    ) -> None:
        self._last_vote = vote
        if signal_ids:
            for tf, sid in signal_ids.items():
                self._signal_ids[(symbol, tf)] = sid

        self._check_kill()
        if self.kill_switch.state.halted:
            return

        for sig in signals:
            key = (sig.symbol, sig.tf_min)
            prev_close = self._last_close.get(key)
            self._last_close[key] = sig.bar.close

            pos = self.broker.get_position(symbol, sig.tf_min)
            # After restart, synced positions may live under tf_min=0
            if pos is None:
                pos = self.broker.get_position(symbol, 0)
            # one-net: also find any TF for this symbol
            if pos is None and self.one_position_per_symbol:
                getter = getattr(self.broker, "get_position_any", None)
                if callable(getter):
                    pos = getter(symbol)
                else:
                    pos = next((p for p in self.broker.get_positions() if p.symbol == symbol), None)
            if pos is not None:
                if self.exit_mode == "book_close" and sig.prev_bar is not None:
                    # §3: exit when close turns against the position
                    if pos.side == Side.LONG and sig.bar.close < sig.prev_bar.close:
                        self._close_position(pos, reason="book_close_turn")
                        continue
                    if pos.side == Side.SHORT and sig.bar.close > sig.prev_bar.close:
                        self._close_position(pos, reason="book_close_turn")
                        continue
                elif self.exit_mode == "hybrid" and sig.kind == SignalKind.FLAT:
                    # Only exit if this FLAT is for the position's TF, or TF unknown (0)
                    if pos.tf_min in (0, sig.tf_min):
                        self._close_position(pos, reason="rule3_flat")
                        continue

            # legacy: also track prev_close for diagnostics
            _ = prev_close

        if vote.dominant == SignalKind.FLAT:
            return

        kind = vote.dominant
        open_positions = self.broker.get_positions()
        if not echelon2_allows_new(open_positions, self.max_drawdown_usd):
            logger.info("echelon2 blocked new entries for %s", symbol)
            if self.store is not None:
                self.store.log_event(
                    "echelon2_block",
                    symbol=symbol,
                    mode=self.mode,
                    detail={
                        "open_positions": len(open_positions),
                        "max_drawdown_usd": self.max_drawdown_usd,
                        "vote": vote.dominant.value,
                    },
                )
            return

        # Signals eligible for this vote on the current bar batch
        eligible = [s for s in vote.signals if s.kind == kind and s.tf_min in {x.tf_min for x in signals if x.kind == kind}]
        if self.one_position_per_symbol and eligible:
            # Bybit one-way: one net position — put full risk on shortest TF
            eligible = sorted(eligible, key=lambda s: s.tf_min)[:1]

        pool = allocate_deposit(
            eligible,
            kind=kind,
            deposit=self.deposit,
            price=price,
            tracker=self.tracker,
            risk_pct=self.tf_risk_pct,
            max_drawdown_pct=self.max_drawdown_usd / self.deposit if self.deposit else 0.1,
            per_trade_risk_pct=self.per_trade_risk_pct,
            max_leverage_frac=self.max_leverage_frac,
        )
        if not pool:
            return

        for alloc in pool:
            self._maybe_enter(alloc, price, prev_bars, vote)
            if self.one_position_per_symbol:
                break

    def _maybe_enter(
        self,
        alloc: Allocation,
        price: float,
        prev_bars: dict[int, Bar],
        vote: VoteResult,
    ) -> None:
        sig = alloc.signal
        key = (sig.symbol, sig.tf_min)

        # Pending conditional order already working for this symbol
        has_pending = getattr(self.broker, "has_pending", None)
        if callable(has_pending) and has_pending(sig.symbol):
            logger.info("skip entry %s — pending stop order on book", sig.symbol)
            return

        # One net position per symbol (Bybit linear one-way)
        existing_any = None
        getter = getattr(self.broker, "get_position_any", None)
        if self.one_position_per_symbol and callable(getter):
            existing_any = getter(sig.symbol)
        else:
            existing_any = self.broker.get_position(sig.symbol, sig.tf_min)

        if existing_any is not None:
            want = Side.LONG if sig.kind == SignalKind.LONG else Side.SHORT
            if existing_any.side == want:
                return
            self._close_position(existing_any, reason="flip")

        if not self.kill_switch.allow_new_order(len(self.broker.get_positions())):
            logger.warning("kill-switch blocked entry: %s", self.kill_switch.state.reason)
            self._check_kill()
            self.broker.cancel_all()
            return

        side = Side.LONG if sig.kind == SignalKind.LONG else Side.SHORT
        prev = sig.prev_bar or prev_bars.get(sig.tf_min)
        if prev is None:
            return

        if side == Side.LONG:
            trigger = alloc.entry_trigger or prev.high
            protect = alloc.protect_stop or prev.low
        else:
            trigger = alloc.entry_trigger or prev.low
            protect = alloc.protect_stop or prev.high

        spec = self.instruments.get(sig.symbol)
        qty = spec.round_qty(alloc.qty)
        if qty <= 0:
            logger.info("qty rounded to 0 for %s tf=%s — skip", sig.symbol, sig.tf_min)
            return
        trigger = spec.round_price(trigger)
        protect = spec.round_price(protect)

        # Bybit reject 110093: conditional Falling needs trigger < last,
        # Rising needs trigger > last. If breakout already happened, enter market.
        already_through = (side == Side.LONG and price >= trigger) or (
            side == Side.SHORT and price <= trigger
        )
        order_type = OrderType.MARKET if already_through else OrderType.STOP_LIMIT
        if already_through:
            logger.info(
                "breakout already through %s %s tf=%s price=%.4f trigger=%.4f → market",
                side.value,
                sig.symbol,
                sig.tf_min,
                price,
                trigger,
            )

        order = Order(
            order_id=str(uuid.uuid4()),
            symbol=sig.symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            price=trigger,
            stop_price=trigger,
            tf_min=sig.tf_min,
            client_tag=f"tf{sig.tf_min}",
        )
        placed = self.broker.place_order(order)
        self.kill_switch.record_order()
        self._check_kill()
        if placed.status in (OrderStatus.FILLED, OrderStatus.WORKING):
            self._pending_entries[key] = placed
            self._pending_protect[sig.symbol] = protect
            entry_type = "market_through" if already_through else "stop_limit"
            self._pending_meta[key] = {
                "vote": self._vote_payload(vote),
                "trigger": trigger,
                "protect_stop": protect,
                "signal_price": price,
                "entry_reason": sig.reason,
                "risk_usd": alloc.risk_usd,
                "already_through": already_through,
                "entry_type": entry_type,
                "order_id": placed.order_id,
            }
            pos = self.broker.get_position(sig.symbol, sig.tf_min)
            if pos is None and callable(getter):
                pos = getter(sig.symbol)
            if pos is not None:
                pos.stop_price = protect
                self._push_exchange_sl(sig.symbol, protect)
            if self.store is not None and pos is not None:
                market = {
                    "price": price,
                    "vote": self._vote_payload(vote),
                    "bar": {
                        "O": sig.bar.open,
                        "H": sig.bar.high,
                        "L": sig.bar.low,
                        "C": sig.bar.close,
                    },
                    "prev": {
                        "O": prev.open,
                        "H": prev.high,
                        "L": prev.low,
                        "C": prev.close,
                    },
                    "k": sig.k,
                    "delta": sig.delta,
                    "checks": sig.checks,
                    "order_type": order.order_type.value,
                    "entry_type": "market_through" if already_through else "stop_limit_filled",
                    "already_through": already_through,
                    "trigger": trigger,
                    "protect_stop": protect,
                    "slippage_vs_trigger": self._slippage_vs_trigger(
                        side, pos.entry_price, trigger
                    ),
                    "risk_usd": alloc.risk_usd,
                    "exit_mode": self.exit_mode,
                    "mode": self.mode,
                    "policy_hash": self.policy_hash,
                }
                self.store.open_trade(
                    symbol=sig.symbol,
                    tf_min=sig.tf_min,
                    side=side.value,
                    qty=pos.qty,
                    entry_price=pos.entry_price,
                    entry_reason=sig.reason,
                    market=market,
                    signal_id=self._signal_ids.get(key),
                    opened_ts_ms=int(time.time() * 1000),
                    mode=self.mode,
                    policy_hash=self.policy_hash,
                )
                self._pending_meta.pop(key, None)
            elif placed.status == OrderStatus.WORKING:
                logger.info(
                    "pending stop entry %s %s tf=%s qty=%s @%s (await reconcile) entry_type=stop_limit",
                    side.value,
                    sig.symbol,
                    sig.tf_min,
                    qty,
                    trigger,
                )
            logger.info(
                "entry %s %s tf=%s qty=%s %s@%s risk$=%.2f mode=%s entry_type=%s | %s",
                side.value,
                sig.symbol,
                sig.tf_min,
                qty,
                order_type.value,
                trigger,
                alloc.risk_usd,
                self.exit_mode,
                entry_type if placed.status == OrderStatus.WORKING else (
                    "market_through" if already_through else "stop_limit_filled"
                ),
                sig.reason,
            )

    def _book_closed_trade(self, trade: ClosedTrade, *, reason: str) -> None:
        trade.closed_ts_ms = trade.closed_ts_ms or int(time.time() * 1000)
        self.tracker.record_pnl(trade.symbol, trade.tf_min, trade.pnl)
        self.validator.add(trade)
        from datetime import datetime, timezone

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.kill_switch.record_pnl(trade.pnl, day)
        self._check_kill()
        if self.store is not None:
            self.store.close_trade(
                symbol=trade.symbol,
                tf_min=trade.tf_min,
                trade=trade,
                exit_reason=reason,
            )
        logger.info(
            "exit %s tf=%s reason=%s pnl=%.4f s=%.6f costs=%.4f",
            trade.symbol,
            trade.tf_min,
            reason,
            trade.pnl,
            trade.s,
            trade.costs_usd,
        )

    def _close_position(self, pos: Position, reason: str) -> None:
        if pos.symbol in self._closing_symbols:
            return
        self._closing_symbols.add(pos.symbol)
        try:
            order = Order(
                order_id=str(uuid.uuid4()),
                symbol=pos.symbol,
                side=pos.side,
                order_type=OrderType.MARKET,
                qty=pos.qty,
                reduce_only=True,
                tf_min=pos.tf_min,
                client_tag=reason,
            )
            before = list(getattr(self.broker, "closed_trades", []))
            placed = self.broker.place_order(order)
            self.kill_switch.record_order()
            self._check_kill()
            self._exchange_sl.pop(pos.symbol, None)
            self._pending_protect.pop(pos.symbol, None)

            after = list(getattr(self.broker, "closed_trades", []))
            if len(after) > len(before):
                # Ghost-finalize after exchange SL → label as exchange_sl
                exit_reason = reason
                if reason == "trailing_stop" and (placed.order_id or "").startswith("ghost-"):
                    exit_reason = "exchange_sl"
                self._book_closed_trade(after[-1], reason=exit_reason)
            else:
                # Hard reject and still local — force-drop to stop spam
                still = self.broker.get_position(pos.symbol, pos.tf_min)
                if still is None:
                    getter = getattr(self.broker, "get_position_any", None)
                    still = getter(pos.symbol) if callable(getter) else None
                if still is not None and placed.status == OrderStatus.REJECTED:
                    logger.error(
                        "close failed for %s — forcing local drop to avoid spam",
                        pos.symbol,
                    )
                    drop = getattr(self.broker, "_finalize_reduce", None)
                    if callable(drop):
                        n = len(getattr(self.broker, "closed_trades", []))
                        drop(order)
                        after2 = list(getattr(self.broker, "closed_trades", []))
                        if len(after2) > n:
                            self._book_closed_trade(after2[-1], reason="force_flat")
                else:
                    logger.info(
                        "exit requested %s tf=%s reason=%s",
                        pos.symbol,
                        pos.tf_min,
                        reason,
                    )
        finally:
            self._closing_symbols.discard(pos.symbol)

    def emergency_stop(self) -> None:
        self.kill_switch.halt("manual_or_engine_emergency")
        self._check_kill()
        self.broker.cancel_all()
        for pos in list(self.broker.get_positions()):
            self._close_position(pos, reason="emergency")
