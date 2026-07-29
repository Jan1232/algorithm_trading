from __future__ import annotations

from typing import Optional

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
