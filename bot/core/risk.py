from __future__ import annotations

from bot.models import Position, Side, Signal


class TfDrawdownTracker:
    """Track equity curve drawdown per (symbol, tf) for risk gate R."""

    def __init__(self) -> None:
        self._peak: dict[tuple[str, int], float] = {}
        self._equity: dict[tuple[str, int], float] = {}

    def record_pnl(self, symbol: str, tf_min: int, pnl: float) -> None:
        key = (symbol, tf_min)
        eq = self._equity.get(key, 0.0) + pnl
        self._equity[key] = eq
        peak = self._peak.get(key, 0.0)
        self._peak[key] = max(peak, eq)

    def drawdown(self, symbol: str, tf_min: int) -> float:
        key = (symbol, tf_min)
        peak = self._peak.get(key, 0.0)
        eq = self._equity.get(key, 0.0)
        if peak <= 0:
            return 0.0 if eq >= 0 else abs(eq)
        return max(0.0, peak - eq)

    def drawdown_pct(self, symbol: str, tf_min: int, deposit: float) -> float:
        if deposit <= 0:
            return 0.0
        return self.drawdown(symbol, tf_min) / deposit


def trailing_stop_price(
    side: Side,
    prev_bar_low: float,
    prev_bar_high: float,
    *,
    buffer_frac: float = 0.0,
    bar_range: float = 0.0,
) -> float:
    """Echelon 1: stop on previous bar extreme, optionally buffered by bar range."""
    buf = max(0.0, float(buffer_frac)) * max(0.0, float(bar_range))
    if side == Side.LONG:
        return prev_bar_low - buf
    if side == Side.SHORT:
        return prev_bar_high + buf
    raise ValueError("flat has no stop")


def potential_loss(position: Position) -> float:
    """Loss if echelon-1 stop is hit (negative or zero)."""
    if position.side == Side.LONG:
        return (position.stop_price - position.entry_price) * position.qty
    if position.side == Side.SHORT:
        return (position.entry_price - position.stop_price) * position.qty
    return 0.0


def echelon2_allows_new(
    open_positions: list[Position],
    max_drawdown_usd: float,
) -> bool:
    """
    Second echelon: sum of potential stop losses (USD) must be >= -P_usd.

    IMPORTANT: pass absolute dollars (deposit * max_drawdown_pct), NOT the
    fraction 0.10. Callers must use Settings.max_drawdown_usd.
    """
    total = sum(potential_loss(p) for p in open_positions)
    return total >= -abs(max_drawdown_usd)


def stop_hit(position: Position, price: float) -> bool:
    if position.side == Side.LONG:
        return price <= position.stop_price
    if position.side == Side.SHORT:
        return price >= position.stop_price
    return False


def filter_signals_by_tf_risk(
    signals: list[Signal],
    tracker: TfDrawdownTracker,
    deposit: float,
    risk_pct: float,
) -> list[Signal]:
    """Keep only TF copies whose drawdown < R."""
    allowed: list[Signal] = []
    for sig in signals:
        dd = tracker.drawdown_pct(sig.symbol, sig.tf_min, deposit)
        if dd < risk_pct:
            allowed.append(sig)
    return allowed
