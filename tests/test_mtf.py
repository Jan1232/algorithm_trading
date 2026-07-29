from bot.core.mtf import MultiTFEngine
from bot.core.allocator import allocate_by_stop_risk
from bot.core.risk import TfDrawdownTracker
from bot.models import Signal, SignalKind, Tick, Bar


def test_mtf_vote_and_bars():
    eng = MultiTFEngine("BTCUSDT", [1, 2])
    base = 0
    price = 100.0
    for i in range(200):
        price += 0.1
        eng.on_tick(Tick("BTCUSDT", price, 1, base + i * 1000))
    vote = eng.vote()
    assert vote.n_total == 2
    assert eng.last_bars


def test_allocator_equal_risk_not_equal_notional():
    tracker = TfDrawdownTracker()
    signals = []
    for tf in (15, 30, 45):
        bar = Bar("BTCUSDT", tf, 100, 110, 90, 108, 0, 1, 1)
        prev = Bar("BTCUSDT", tf, 100, 105, 95, 102, 0, 1, 1)
        signals.append(
            Signal(SignalKind.LONG, "BTCUSDT", tf, bar, prev, k=0.4, delta=12)
        )

    allocs = allocate_by_stop_risk(
        signals,
        kind=SignalKind.LONG,
        deposit=9000,
        price=100,
        tracker=tracker,
        risk_pct=0.1,
        max_drawdown_pct=0.10,
    )
    assert len(allocs) == 3
    # Equal risk budgets
    assert all(abs(a.risk_usd - allocs[0].risk_usd) < 1e-9 for a in allocs)
    # Same protect distance => same qty
    assert all(abs(a.qty - allocs[0].qty) < 1e-9 for a in allocs)


def test_allocator_excludes_risky_tf():
    tracker = TfDrawdownTracker()
    tracker.record_pnl("BTCUSDT", 15, 100)
    tracker.record_pnl("BTCUSDT", 15, -200)
    signals = []
    for tf in (15, 30):
        bar = Bar("BTCUSDT", tf, 100, 110, 90, 108, 0, 1, 1)
        prev = Bar("BTCUSDT", tf, 100, 105, 95, 102, 0, 1, 1)
        signals.append(Signal(SignalKind.LONG, "BTCUSDT", tf, bar, prev))

    allocs = allocate_by_stop_risk(
        signals,
        kind=SignalKind.LONG,
        deposit=1000,
        price=100,
        tracker=tracker,
        risk_pct=0.05,
        max_drawdown_pct=0.10,
    )
    assert len(allocs) == 1
    assert allocs[0].signal.tf_min == 30
