"""Execution cost model for realistic Mo (fees + slippage + funding)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """
    Bybit linear-ish defaults (taker). Applied on every fill.
    funding_bps_per_8h applied over hold_hours / 8 on the held position notional.
    """

    taker_fee_bps: float = 5.5  # 0.055%
    slippage_bps: float = 5.0  # 0.05% adverse
    funding_bps_per_8h: float = 1.0  # ~0.01%/8h; applied when hold_hours given

    def fee_usd(self, notional: float) -> float:
        return abs(notional) * self.taker_fee_bps / 10_000.0

    def slip_price(self, price: float, *, side_buy: bool) -> float:
        """
        Adverse slip on price (used by paper fills).

        TODO: round_trip_costs_usd approximates slippage as bps on notional
        instead of this price adjustment — two mechanisms coexist intentionally
        for paper vs accounting; do not diverge their bps defaults.
        """
        mult = 1.0 + self.slippage_bps / 10_000.0
        if side_buy:
            return price * mult
        return price / mult

    def funding_usd(self, position_notional: float, hold_hours: float) -> float:
        """Funding on held position notional over hold_hours / 8 periods."""
        if hold_hours <= 0 or self.funding_bps_per_8h <= 0:
            return 0.0
        periods = hold_hours / 8.0
        return abs(position_notional) * (self.funding_bps_per_8h / 10_000.0) * periods

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
        # Funding on the HELD position notional (entry), not avg entry/exit
        funding = self.funding_usd(entry_notional, hold_hours)
        return fees, slip, funding
