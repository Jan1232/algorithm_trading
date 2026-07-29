"""
Order-flow collectors (aggressor volume / delta / footprint).

Data collection for later falsification of volume/delta hypotheses — NOT a
confirmed strategy improvement. Does not change SignalCore / vote / allocator /
orders / policy_hash.

CVD is NOT stored: compute as running sum of ``delta`` on read (restarts-safe).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bot.core.bars import bucket_start
from bot.models import Tick


@dataclass(frozen=True)
class OrderFlowBar:
    symbol: str
    tf_min: int
    start_ts_ms: int
    end_ts_ms: int
    buy_vol: float
    sell_vol: float
    unknown_vol: float
    delta: float
    # price_bucket -> (buy_vol, sell_vol)
    footprint: dict[float, tuple[float, float]] = field(default_factory=dict)

    def footprint_rows(self) -> list[dict]:
        rows = []
        for bucket, (b, s) in sorted(self.footprint.items()):
            rows.append(
                {
                    "symbol": self.symbol,
                    "tf_min": self.tf_min,
                    "start_ts_ms": self.start_ts_ms,
                    "price_bucket": bucket,
                    "buy_vol": b,
                    "sell_vol": s,
                }
            )
        return rows


class OrderFlowBuilder:
    """
    Aggregate public-trade aggressor volume into fixed TF buckets.

    Separate from TickBarBuilder so the trading OHLC path stays untouched.
    Bucket boundaries reuse ``bucket_start`` (same as TickBarBuilder).
    """

    def __init__(
        self,
        symbol: str,
        dt_min: int,
        *,
        price_bucket_bps: float = 1.0,
    ) -> None:
        if dt_min <= 0:
            raise ValueError("dt_min must be positive")
        if price_bucket_bps <= 0:
            raise ValueError("price_bucket_bps must be positive")
        self.symbol = symbol
        self.dt_min = dt_min
        self.dt_ms = dt_min * 60_000
        self.price_bucket_bps = price_bucket_bps
        self._start_ts_ms: Optional[int] = None
        self._buy = 0.0
        self._sell = 0.0
        self._unknown = 0.0
        self._fp: dict[float, list[float]] = {}  # bucket -> [buy, sell]

    def _price_bucket(self, price: float) -> float:
        # Round to nearest step of price_bucket_bps of price
        step = abs(price) * self.price_bucket_bps / 10_000.0
        if step <= 0:
            return price
        return round(price / step) * step

    def _reset(self) -> None:
        self._start_ts_ms = None
        self._buy = 0.0
        self._sell = 0.0
        self._unknown = 0.0
        self._fp = {}

    def _flush(self, end_ts_ms: int) -> OrderFlowBar:
        assert self._start_ts_ms is not None
        footprint = {k: (v[0], v[1]) for k, v in self._fp.items()}
        bar = OrderFlowBar(
            symbol=self.symbol,
            tf_min=self.dt_min,
            start_ts_ms=self._start_ts_ms,
            end_ts_ms=end_ts_ms,
            buy_vol=self._buy,
            sell_vol=self._sell,
            unknown_vol=self._unknown,
            delta=self._buy - self._sell,
            footprint=footprint,
        )
        self._reset()
        return bar

    def on_tick(self, tick: Tick) -> Optional[OrderFlowBar]:
        if tick.symbol != self.symbol:
            raise ValueError(f"tick symbol {tick.symbol} != builder {self.symbol}")

        bstart = bucket_start(tick.ts_ms, self.dt_ms)
        closed: Optional[OrderFlowBar] = None
        if self._start_ts_ms is not None and bstart != self._start_ts_ms:
            closed = self._flush(end_ts_ms=bstart)

        if self._start_ts_ms is None:
            self._start_ts_ms = bstart

        size = float(tick.size or 0.0)
        agg = tick.aggressor
        if agg == "Buy":
            self._buy += size
            side_buy = True
        elif agg == "Sell":
            self._sell += size
            side_buy = False
        else:
            self._unknown += size
            side_buy = None

        if side_buy is not None and size > 0:
            bucket = self._price_bucket(tick.price)
            slot = self._fp.setdefault(bucket, [0.0, 0.0])
            if side_buy:
                slot[0] += size
            else:
                slot[1] += size

        return closed
