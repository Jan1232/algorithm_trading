from __future__ import annotations

import time
from typing import Any, Callable, Optional

from bot.core.bars import TickBarBuilder
from bot.core.signals import SignalCore
from bot.models import Bar, Signal, SignalKind, Tick, VoteResult


class MultiTFEngine:
    """Independent signal copies per timeframe + voting N+/N-/N0."""

    def __init__(
        self,
        symbol: str,
        timeframes_min: list[int],
        *,
        vote_min_directional: int = 2,
        vote_min_margin: int = 2,
    ) -> None:
        self.symbol = symbol
        self.timeframes_min = list(timeframes_min)
        self.vote_min_directional = vote_min_directional
        self.vote_min_margin = vote_min_margin
        self.builders = {tf: TickBarBuilder(symbol, tf) for tf in self.timeframes_min}
        self.cores = {tf: SignalCore() for tf in self.timeframes_min}
        self.last_signals: dict[int, Signal] = {}
        self.last_bars: dict[int, Bar] = {}

    def restore(
        self,
        store: Any,
        *,
        fetch_kline: Optional[Callable[[str, int], Optional[dict]]] = None,
        now_ms: Optional[int] = None,
    ) -> dict[str, int]:
        """
        Restore process state after restart.

        Part A (always): seed SignalCore._prev + last_bars from closed bars in DB.
        Part B (optional): seed in-progress TickBarBuilder from REST kline via fetch_kline.
        """
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        stats = {"prev_seeded": 0, "partial_seeded": 0, "partial_skipped": 0}
        for tf in self.timeframes_min:
            last = store.last_bar(self.symbol, tf)
            if last is not None:
                self.cores[tf].seed_prev(last)
                self.last_bars[tf] = last
                stats["prev_seeded"] += 1
            if fetch_kline is None:
                continue
            candle = fetch_kline(self.symbol, tf)
            if candle is None:
                stats["partial_skipped"] += 1
                continue
            ok = self.builders[tf].seed_partial(
                open_=float(candle["open"]),
                high=float(candle["high"]),
                low=float(candle["low"]),
                close=float(candle["close"]),
                start_ts_ms=int(candle["start_ts_ms"]),
                tick_count=int(candle.get("tick_count") or 0),
                now_ms=now,
            )
            if ok:
                stats["partial_seeded"] += 1
            else:
                stats["partial_skipped"] += 1
        return stats

    def on_tick(self, tick: Tick) -> list[Signal]:
        if tick.symbol != self.symbol:
            return []
        closed_signals: list[Signal] = []
        for tf, builder in self.builders.items():
            bar = builder.on_tick(tick)
            if bar is None:
                continue
            self.last_bars[tf] = bar
            signal = self.cores[tf].on_bar(bar)
            if signal is None:
                continue
            self.last_signals[tf] = signal
            closed_signals.append(signal)
        return closed_signals

    def vote(self) -> VoteResult:
        result = VoteResult(
            min_directional=self.vote_min_directional,
            min_margin=self.vote_min_margin,
        )
        for tf in self.timeframes_min:
            sig = self.last_signals.get(tf)
            if sig is None:
                result.n_none += 1
                continue
            result.signals.append(sig)
            if sig.kind == SignalKind.LONG:
                result.n_long += 1
            elif sig.kind == SignalKind.SHORT:
                result.n_short += 1
            else:
                result.n_flat += 1
        return result

    def signals_by_kind(self, kind: SignalKind) -> list[Signal]:
        return [s for s in self.last_signals.values() if s.kind == kind]

    def get_prev_bar(self, tf_min: int) -> Optional[Bar]:
        return self.last_bars.get(tf_min)
