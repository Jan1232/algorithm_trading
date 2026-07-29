"""Prefreeze economics pack: funding + pass-criteria gate."""

from __future__ import annotations

from bot.core.costs import CostModel
from bot.experiment import PassCriteria, evaluate_pass


def test_funding_uses_entry_notional_over_hold_periods():
    """24h hold → 3×8h windows; funding = notional × 1bps × 3 (not avg entry/exit)."""
    costs = CostModel(taker_fee_bps=0.0, slippage_bps=0.0, funding_bps_per_8h=1.0)
    qty = 1.0
    entry = 100.0
    exit_px = 200.0  # if avg were used, funding would be higher
    fees, slip, funding = costs.round_trip_costs_usd(
        qty, entry, exit_px, hold_hours=24.0
    )
    assert fees == 0.0
    assert slip == 0.0
    expected = abs(qty * entry) * (1.0 / 10_000.0) * (24.0 / 8.0)
    assert abs(funding - expected) < 1e-12
    # Must NOT use average notional (150)
    avg_wrong = abs(qty * (entry + exit_px) / 2.0) * (1.0 / 10_000.0) * 3.0
    assert abs(funding - avg_wrong) > 1e-9


def test_pass_criteria_ignores_echelon2_block_rate_when_disabled():
    c = PassCriteria(echelon2_block_rate_gate_enabled=False)
    assert c.echelon2_block_rate_gate_enabled is False
    ev = evaluate_pass(
        trades_closed=200,
        mo=0.01,
        tf_baskets_positive_mo_pct=0.80,
        max_drawdown_pct=0.05,
        echelon2_block_rate=0.0,  # would FAIL old [1%,50%] gate
        criteria=c,
        monkey_status="pass",
    )
    assert ev.passed is True
    assert "echelon2_block_rate" not in " ".join(ev.reasons)


def test_pass_criteria_echelon2_gate_still_works_when_enabled():
    c = PassCriteria(echelon2_block_rate_gate_enabled=True)
    ev = evaluate_pass(
        trades_closed=200,
        mo=0.01,
        tf_baskets_positive_mo_pct=0.80,
        max_drawdown_pct=0.05,
        echelon2_block_rate=0.0,
        criteria=c,
        monkey_status="pass",
    )
    assert ev.passed is False
    assert any("echelon2_block_rate" in r for r in ev.reasons)
