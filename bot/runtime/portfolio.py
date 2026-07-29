from __future__ import annotations

import logging
from typing import Optional

from bot.config import Settings
from bot.core.bars import TickBarBuilder
from bot.core.liquidity import LiquidityFilter
from bot.core.mtf import MultiTFEngine
from bot.analysis.orderflow import OrderFlowBuilder
from bot.models import Tick
from bot.orders.manager import OrderManager
from bot.storage.db import TradeStore

logger = logging.getLogger(__name__)


class SymbolWorker:
    def __init__(
        self,
        symbol: str,
        settings: Settings,
        order_manager: OrderManager,
        liquidity: LiquidityFilter,
        store: Optional[TradeStore] = None,
    ) -> None:
        self.symbol = symbol
        self.settings = settings
        self.mtf = MultiTFEngine(
            symbol,
            settings.timeframes_min,
            vote_min_directional=settings.vote_min_directional,
            vote_min_margin=settings.vote_min_margin,
        )
        self.order_manager = order_manager
        self.liquidity = liquidity
        self.store = store
        self.last_price: Optional[float] = None
        # Record-only short-TF collectors — never feed SignalCore / vote / allocator.
        self.shadow_builders = {
            tf: TickBarBuilder(symbol, tf) for tf in (settings.shadow_timeframes_min or [])
        }
        of_tfs = (
            list(settings.orderflow_tf_min)
            if settings.orderflow_collect and settings.orderflow_tf_min
            else []
        )
        self.orderflow_builders = {
            tf: OrderFlowBuilder(
                symbol,
                tf,
                price_bucket_bps=settings.orderflow_price_bucket_bps,
            )
            for tf in of_tfs
        }

    def on_tick(self, tick: Tick) -> None:
        # Shadow first: pure bar persistence, zero trading side-effects.
        for _tf, builder in self.shadow_builders.items():
            closed_shadow = builder.on_tick(tick)
            if closed_shadow is not None and self.store is not None:
                self.store.save_shadow_bar(closed_shadow)

        for _tf, of_builder in self.orderflow_builders.items():
            of_bar = of_builder.on_tick(tick)
            if of_bar is not None and self.store is not None:
                self.store.save_orderflow_bar(of_bar)
                self.store.save_footprint_rows(of_bar.footprint_rows())

        self.liquidity.on_tick(tick)
        self.last_price = tick.price
        self.order_manager.broker.mark_price(tick.symbol, tick.price)
        self.order_manager.on_price(tick.symbol, tick.price, self.mtf.last_bars)

        closed = self.mtf.on_tick(tick)
        if not closed:
            return

        vote = self.mtf.vote()
        signal_ids: dict[int, int] = {}
        for sig in closed:
            # Persist closed bar + signal evaluation (rules 1–3 with checks)
            bar = self.mtf.last_bars.get(sig.tf_min)
            if self.store is not None:
                if bar is not None:
                    self.store.save_bar(bar)
                sid = self.store.save_signal(sig, vote, mode=self.settings.mode)
                signal_ids[sig.tf_min] = sid
            logger.info(
                "signal %s %s tf=%s | %s | vote L=%d S=%d F=%d N=%d",
                sig.kind.value,
                sig.symbol,
                sig.tf_min,
                sig.reason,
                vote.n_long,
                vote.n_short,
                vote.n_flat,
                vote.n_none,
            )

        self.order_manager.on_signals(
            symbol=tick.symbol,
            price=tick.price,
            signals=closed,
            vote=vote,
            prev_bars=self.mtf.last_bars,
            signal_ids=signal_ids,
        )


class Portfolio:
    def __init__(
        self,
        settings: Settings,
        order_manager: OrderManager,
        store: Optional[TradeStore] = None,
    ) -> None:
        self.settings = settings
        self.order_manager = order_manager
        self.store = store
        self.liquidity = LiquidityFilter(settings.min_ticks_per_sec)
        self.workers = {
            sym: SymbolWorker(sym, settings, order_manager, self.liquidity, store)
            for sym in settings.symbols
        }

    def on_tick(self, tick: Tick) -> None:
        worker = self.workers.get(tick.symbol)
        if worker is None:
            return
        worker.on_tick(tick)
