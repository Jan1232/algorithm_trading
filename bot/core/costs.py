"""Execution cost model for realistic Mo (fees + slippage; funding stub)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """
    Bybit linear-ish defaults (taker). Applied on every fill.
    funding_bps_per_8h: reserved for later hold-time adjustment (stub=0 until wired).
    """

    taker_fee_bps: float = 5.5  # 0.055%
    slippage_bps: float = 5.0  # 0.05% adverse
    funding_bps_per_8h: float = 1.0  # stub default ~0.01%/8h; applied only if hold_hours given

    def fee_usd(self, notional: float) -> float:
        return abs(notional) * self.taker_fee_bps / 10_000.0

    def slip_price(self, price: float, *, side_buy: bool) -> float:
        """Adverse slip: buy pays more, sell receives less."""
        mult = 1.0 + self.slippage_bps / 10_000.0
        if side_buy:
            return price * mult
        return price / mult

    def funding_usd(self, notional: float, hold_hours: float) -> float:
        if hold_hours <= 0 or self.funding_bps_per_8h <= 0:
            return 0.0
        periods = hold_hours / 8.0
        return abs(notional) * (self.funding_bps_per_8h / 10_000.0) * periods

    def round_trip_costs_usd(
        self,
        qty: float,
        entry_price: float,
        exit_price: float,
        *,
        hold_hours: float = 0.0,
    ) -> tuple[float, float, float]:
        """Return (fees, slippage_approx, funding) in USD."""
        entry_notional = qty * entry_price
        exit_notional = qty * exit_price
        fees = self.fee_usd(entry_notional) + self.fee_usd(exit_notional)
        # Approximate slippage cost as bps on both notionals
        slip = (
            abs(entry_notional) * self.slippage_bps / 10_000.0
            + abs(exit_notional) * self.slippage_bps / 10_000.0
        )
        funding = self.funding_usd((entry_notional + exit_notional) / 2.0, hold_hours)
        return fees, slip, funding
