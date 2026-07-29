from __future__ import annotations

from collections import deque
from typing import Deque

from bot.models import Tick


class LiquidityFilter:
    """Select instruments by tick density (ticks per second)."""

    def __init__(self, min_ticks_per_sec: float, window_sec: float = 60.0) -> None:
        self.min_ticks_per_sec = min_ticks_per_sec
        self.window_ms = int(window_sec * 1000)
        self._ticks: dict[str, Deque[int]] = {}

    def on_tick(self, tick: Tick) -> None:
        q = self._ticks.setdefault(tick.symbol, deque())
        q.append(tick.ts_ms)
        cutoff = tick.ts_ms - self.window_ms
        while q and q[0] < cutoff:
            q.popleft()

    def ticks_per_sec(self, symbol: str) -> float:
        q = self._ticks.get(symbol)
        if not q:
            return 0.0
        return len(q) / (self.window_ms / 1000.0)

    def is_liquid(self, symbol: str) -> bool:
        return self.ticks_per_sec(symbol) >= self.min_ticks_per_sec
