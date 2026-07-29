from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class OrderType(str, Enum):
    MARKET = "market"
    STOP_LIMIT = "stop_limit"
    STOP_MARKET = "stop_market"


class OrderStatus(str, Enum):
    NEW = "new"
    WORKING = "working"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class SignalKind(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass(frozen=True)
class Tick:
    symbol: str
    price: float
    size: float
    ts_ms: int


@dataclass(frozen=True)
class Bar:
    symbol: str
    tf_min: int
    open: float
    high: float
    low: float
    close: float
    start_ts_ms: int
    end_ts_ms: int
    tick_count: int = 0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass(frozen=True)
class Signal:
    kind: SignalKind
    symbol: str
    tf_min: int
    bar: Bar
    prev_bar: Optional[Bar]
    k: float = 0.0
    delta: float = 0.0
    reason: str = ""
    checks: dict = field(default_factory=dict)


@dataclass
class Order:
    order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    qty: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    reduce_only: bool = False
    status: OrderStatus = OrderStatus.NEW
    tf_min: Optional[int] = None
    client_tag: str = ""


@dataclass
class Position:
    symbol: str
    side: Side
    qty: float
    entry_price: float
    tf_min: int
    stop_price: float
    opened_ts_ms: int = 0
    unrealized_pnl: float = 0.0


@dataclass
class ClosedTrade:
    symbol: str
    side: Side
    tf_min: int
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    opened_ts_ms: int
    closed_ts_ms: int
    fees_usd: float = 0.0
    slippage_usd: float = 0.0
    funding_usd: float = 0.0

    @property
    def costs_usd(self) -> float:
        return self.fees_usd + self.slippage_usd + self.funding_usd

    @property
    def s(self) -> float:
        """
        Net per-unit outcome after costs.
        Gross: long exit-entry; short entry-exit. Costs spread per unit of qty.
        """
        if self.side == Side.LONG:
            gross = self.exit_price - self.entry_price
        else:
            gross = self.entry_price - self.exit_price
        cost_pu = (self.costs_usd / self.qty) if self.qty else 0.0
        return gross - cost_pu


@dataclass
class VoteResult:
    n_long: int = 0
    n_short: int = 0
    n_flat: int = 0  # explicit FLAT signal only
    n_none: int = 0  # TF bar not closed yet / no signal
    min_directional: int = 2
    min_margin: int = 2
    signals: list[Signal] = field(default_factory=list)

    @property
    def n_directional(self) -> int:
        return self.n_long + self.n_short

    @property
    def n_voting(self) -> int:
        """TFs that actually voted (long/short/flat), excluding n_none."""
        return self.n_long + self.n_short + self.n_flat

    @property
    def n_total(self) -> int:
        return self.n_long + self.n_short + self.n_flat + self.n_none

    @property
    def dominant(self) -> SignalKind:
        """Gated majority: need >=min_directional and margin over opposite side."""
        if (
            self.n_long >= self.min_directional
            and (self.n_long - self.n_short) >= self.min_margin
        ):
            return SignalKind.LONG
        if (
            self.n_short >= self.min_directional
            and (self.n_short - self.n_long) >= self.min_margin
        ):
            return SignalKind.SHORT
        return SignalKind.FLAT
