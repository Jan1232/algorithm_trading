"""Instrument lot size / tick size helpers for Bybit linear."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback when API unavailable (paper / offline)
_FALLBACK: dict[str, tuple[float, float, float]] = {
    # symbol: (qty_step, min_qty, tick_size)
    "BTCUSDT": (0.001, 0.001, 0.1),
    "ETHUSDT": (0.01, 0.01, 0.01),
    "SOLUSDT": (0.1, 0.1, 0.01),
}


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    qty_step: float
    min_qty: float
    tick_size: float
    min_notional: float = 5.0

    def round_qty(self, qty: float) -> float:
        if qty <= 0 or self.qty_step <= 0:
            return 0.0
        steps = math.floor(qty / self.qty_step + 1e-12)
        q = steps * self.qty_step
        # avoid float dust
        decimals = max(0, int(round(-math.log10(self.qty_step))) if self.qty_step < 1 else 0)
        q = round(q, decimals)
        if q < self.min_qty:
            return 0.0
        return q

    def round_price(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        ticks = round(price / self.tick_size)
        return ticks * self.tick_size


class InstrumentRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, InstrumentSpec] = {}
        for sym, (step, mn, tick) in _FALLBACK.items():
            self._specs[sym] = InstrumentSpec(sym, step, mn, tick)

    def get(self, symbol: str) -> InstrumentSpec:
        if symbol in self._specs:
            return self._specs[symbol]
        # conservative default
        return InstrumentSpec(symbol, 0.001, 0.001, 0.01)

    def load_fallback(self, symbols: list[str]) -> None:
        for sym in symbols:
            _ = self.get(sym)

    def load_from_bybit(self, http, *, category: str = "linear", symbols: Optional[list[str]] = None) -> None:
        """Populate from GET /v5/market/instruments-info."""
        try:
            resp = http.get_instruments_info(category=category, limit=1000)
            rows = ((resp.get("result") or {}).get("list")) or []
            want = set(symbols) if symbols else None
            for row in rows:
                sym = row.get("symbol")
                if not sym:
                    continue
                if want is not None and sym not in want:
                    continue
                lot = row.get("lotSizeFilter") or {}
                price_f = row.get("priceFilter") or {}
                step = float(lot.get("qtyStep") or 0.001)
                min_qty = float(lot.get("minOrderQty") or step)
                tick = float(price_f.get("tickSize") or 0.01)
                min_notional = float(lot.get("minNotionalValue") or 5.0)
                self._specs[sym] = InstrumentSpec(sym, step, min_qty, tick, min_notional)
                logger.info(
                    "instrument %s qty_step=%s min_qty=%s tick=%s",
                    sym,
                    step,
                    min_qty,
                    tick,
                )
        except Exception:
            logger.exception("failed to load instruments; using fallback specs")
