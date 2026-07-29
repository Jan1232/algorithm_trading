from bot.core.allocator import allocate_by_stop_risk
from bot.core.risk import TfDrawdownTracker, echelon2_allows_new, potential_loss
from bot.config import load_settings
from bot.models import Bar, Position, Side, Signal, SignalKind


def test_echelon2_uses_usd_not_fraction():
    """P is absolute USD (deposit * max_drawdown_pct), not the raw fraction."""
    s = load_settings()
    assert abs(s.max_drawdown_usd - s.deposit_usd * s.max_drawdown_pct) < 1e-9
    assert s.max_drawdown_usd == s.deposit_usd * 0.10

    positions = [
        Position("BTCUSDT", Side.LONG, 10, 100, 15, stop_price=90),  # -100
    ]
    # With absolute USD cap from config, -$100 is allowed when cap >= 100
    if s.max_drawdown_usd >= 100:
        assert echelon2_allows_new(positions, s.max_drawdown_usd)
    # If someone wrongly passed fraction 0.10, -$100 would ALWAYS block
    assert not echelon2_allows_new(positions, 0.10)


def test_allocate_by_stop_risk_sizes_by_distance():
    tracker = TfDrawdownTracker()
    signals = []
    for tf, prev_low in ((15, 90.0), (60, 50.0)):
        bar = Bar("BTCUSDT", tf, 100, 110, 90, 108, 0, 1, 1)
        prev = Bar("BTCUSDT", tf, 100, 105, prev_low, 102, 0, 1, 1)
        signals.append(Signal(SignalKind.LONG, "BTCUSDT", tf, bar, prev, k=0.6, delta=4))

    allocs = allocate_by_stop_risk(
        signals,
        kind=SignalKind.LONG,
        deposit=10_000,
        price=100,
        tracker=tracker,
        risk_pct=0.1,
        per_trade_risk_pct=0.02,
    )
    assert len(allocs) == 2
    # Equal per-trade risk budgets
    assert abs(allocs[0].risk_usd - allocs[1].risk_usd) < 1e-6
    assert abs(allocs[0].risk_usd - 200.0) < 1e-6
    # Wider stop distance => smaller qty
    by_tf = {a.signal.tf_min: a for a in allocs}
    # Wider protective distance (tf60: 100-50=50 vs tf15: 100-90=10) => smaller qty
    assert by_tf[15].qty > by_tf[60].qty
    assert by_tf[15].protect_stop == 90.0
    assert by_tf[60].protect_stop == 50.0
    assert by_tf[15].entry_trigger == 105.0
