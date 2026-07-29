from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class KillSwitchState:
    halted: bool = False
    reason: str = ""
    order_timestamps: deque[float] = field(default_factory=deque)
    daily_pnl: float = 0.0
    day_key: str = ""


class KillSwitch:
    """Hard limits on order rate, open positions, and daily loss."""

    def __init__(
        self,
        *,
        max_orders_per_minute: int,
        max_open_positions: int,
        max_daily_loss_pct: float,
        deposit: float,
    ) -> None:
        self.max_orders_per_minute = max_orders_per_minute
        self.max_open_positions = max_open_positions
        self.max_daily_loss_usd = deposit * max_daily_loss_pct
        self.state = KillSwitchState()

    def halt(self, reason: str) -> None:
        self.state.halted = True
        self.state.reason = reason

    def reset(self) -> None:
        self.state = KillSwitchState()

    def record_order(self) -> None:
        now = time.time()
        self.state.order_timestamps.append(now)
        self._trim(now)
        if len(self.state.order_timestamps) > self.max_orders_per_minute:
            self.halt(f"order rate > {self.max_orders_per_minute}/min")

    def record_pnl(self, pnl: float, day_key: str) -> None:
        if self.state.day_key != day_key:
            self.state.day_key = day_key
            self.state.daily_pnl = 0.0
        self.state.daily_pnl += pnl
        if self.state.daily_pnl <= -abs(self.max_daily_loss_usd):
            self.halt(f"daily loss {self.state.daily_pnl:.2f} exceeded limit")

    def allow_new_order(self, open_positions: int) -> bool:
        if self.state.halted:
            return False
        if open_positions >= self.max_open_positions:
            self.halt(f"open positions >= {self.max_open_positions}")
            return False
        now = time.time()
        self._trim(now)
        if len(self.state.order_timestamps) >= self.max_orders_per_minute:
            self.halt(f"order rate limit {self.max_orders_per_minute}/min")
            return False
        return True

    def _trim(self, now: float) -> None:
        cutoff = now - 60.0
        while self.state.order_timestamps and self.state.order_timestamps[0] < cutoff:
            self.state.order_timestamps.popleft()
