from __future__ import annotations

from typing import Optional

from bot.models import Bar, Tick


def bucket_start(ts_ms: int, dt_ms: int) -> int:
    """Align timestamp to fixed bar bucket (shared by OHLC and order-flow)."""
    return (ts_ms // dt_ms) * dt_ms


class TickBarBuilder:
    """Aggregate ticks into OHLC bars of fixed duration dt_min."""

    def __init__(self, symbol: str, dt_min: int) -> None:
        if dt_min <= 0:
            raise ValueError("dt_min must be positive")
        self.symbol = symbol
        self.dt_min = dt_min
        self.dt_ms = dt_min * 60_000
        self._open: Optional[float] = None
        self._high: Optional[float] = None
        self._low: Optional[float] = None
        self._close: Optional[float] = None
        self._start_ts_ms: Optional[int] = None
        self._tick_count = 0

    def _bucket_start(self, ts_ms: int) -> int:
        return bucket_start(ts_ms, self.dt_ms)

    def _flush(self, end_ts_ms: int) -> Bar:
        assert self._open is not None
        assert self._high is not None
        assert self._low is not None
        assert self._close is not None
        assert self._start_ts_ms is not None
        bar = Bar(
            symbol=self.symbol,
            tf_min=self.dt_min,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            start_ts_ms=self._start_ts_ms,
            end_ts_ms=end_ts_ms,
            tick_count=self._tick_count,
        )
        self._open = None
        self._high = None
        self._low = None
        self._close = None
        self._start_ts_ms = None
        self._tick_count = 0
        return bar

    def seed_partial(
        self,
        *,
        open_: float,
        high: float,
        low: float,
        close: float,
        start_ts_ms: int,
        tick_count: int = 0,
        now_ms: Optional[int] = None,
    ) -> bool:
        """
        Approximate restore of an in-progress bar (e.g. from REST kline).

        Only seeds when the builder is empty. If now_ms is given, start_ts_ms
        must match the current bucket — otherwise refuse (avoid mixing buckets).
        Returns True if state was seeded.
        """
        if self._start_ts_ms is not None:
            return False
        if now_ms is not None and self._bucket_start(now_ms) != start_ts_ms:
            return False
        self._open = open_
        self._high = high
        self._low = low
        self._close = close
        self._start_ts_ms = start_ts_ms
        self._tick_count = tick_count
        return True

    def on_tick(self, tick: Tick) -> Optional[Bar]:
        if tick.symbol != self.symbol:
            raise ValueError(f"tick symbol {tick.symbol} != builder {self.symbol}")

        bucket = self._bucket_start(tick.ts_ms)
        closed: Optional[Bar] = None

        if self._start_ts_ms is not None and bucket != self._start_ts_ms:
            closed = self._flush(end_ts_ms=bucket)

        if self._start_ts_ms is None:
            self._start_ts_ms = bucket
            self._open = tick.price
            self._high = tick.price
            self._low = tick.price
            self._close = tick.price
            self._tick_count = 1
        else:
            assert self._high is not None and self._low is not None
            self._high = max(self._high, tick.price)
            self._low = min(self._low, tick.price)
            self._close = tick.price
            self._tick_count += 1

        return closed

    def flush(self, end_ts_ms: Optional[int] = None) -> Optional[Bar]:
        if self._start_ts_ms is None:
            return None
        return self._flush(end_ts_ms=end_ts_ms if end_ts_ms is not None else self._start_ts_ms + self.dt_ms)
