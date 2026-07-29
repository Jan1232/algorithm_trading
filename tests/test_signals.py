from bot.core.signals import SignalCore, compute_delta, evaluate_signal
from bot.models import Bar, SignalKind


def _bar(o, h, l, c, tf=15, start=0):
    return Bar(
        symbol="BTCUSDT",
        tf_min=tf,
        open=o,
        high=h,
        low=l,
        close=c,
        start_ts_ms=start,
        end_ts_ms=start + tf * 60_000,
        tick_count=10,
    )


def test_delta_from_bar_no_params():
    bar = _bar(100, 110, 90, 108)
    k, d = compute_delta(bar, for_long=True)
    # k = (108-100)/(110-90) = 0.4, delta = 20 * 0.6 = 12
    assert abs(k - 0.4) < 1e-9
    assert abs(d - 12.0) < 1e-9


def test_long_rule():
    prev = _bar(100, 105, 95, 102, start=0)
    # HH, HL, mid > L+delta with strong close → small delta
    cur = _bar(102, 120, 100, 118, start=15 * 60_000)
    sig = evaluate_signal(cur, prev)
    assert sig.kind == SignalKind.LONG


def test_short_rule():
    prev = _bar(100, 105, 95, 98, start=0)
    cur = _bar(98, 100, 80, 82, start=15 * 60_000)
    sig = evaluate_signal(cur, prev)
    assert sig.kind == SignalKind.SHORT


def test_flat_when_inside_bar():
    prev = _bar(100, 110, 90, 105, start=0)
    cur = _bar(104, 108, 92, 100, start=15 * 60_000)
    sig = evaluate_signal(cur, prev)
    assert sig.kind == SignalKind.FLAT


def test_zero_range_bar_safe():
    prev = _bar(100, 100, 100, 100, start=0)
    cur = _bar(100, 100, 100, 100, start=15 * 60_000)
    sig = evaluate_signal(cur, prev)
    assert sig.kind == SignalKind.FLAT


def test_rule1_equivalent_to_k_gt_half():
    """Reviewer identity: mid > L+Delta  <=>  k_long > 0.5 (same bar)."""
    prev = _bar(100, 105, 95, 102, start=0)
    # close in upper half, HH&HL
    cur = _bar(102, 120, 100, 118, start=15 * 60_000)
    sig = evaluate_signal(cur, prev)
    k, d = compute_delta(cur, for_long=True)
    assert k > 0.5
    assert sig.kind == SignalKind.LONG
    assert sig.checks["rule1_long"]["passed"] is True
    assert sig.checks["rule1_long"]["equiv_passed"] is True
    assert sig.checks["rule1_long"]["passed"] == sig.checks["rule1_long"]["equiv_passed"]

    # HH&HL but close in lower half => k<=0.5 => not long
    weak = _bar(110, 120, 100, 105, start=15 * 60_000)
    k_w, _ = compute_delta(weak, for_long=True)
    assert k_w <= 0.5
    sig_w = evaluate_signal(weak, prev)
    assert sig_w.kind != SignalKind.LONG
    assert sig_w.checks["rule1_long"]["passed"] == sig_w.checks["rule1_long"]["equiv_passed"]


def test_rule2_equivalent_to_k_gt_half():
    prev = _bar(100, 105, 95, 98, start=0)
    cur = _bar(98, 100, 80, 82, start=15 * 60_000)
    sig = evaluate_signal(cur, prev)
    k, _ = compute_delta(cur, for_long=False)
    assert k > 0.5
    assert sig.kind == SignalKind.SHORT
    assert sig.checks["rule2_short"]["passed"] == sig.checks["rule2_short"]["equiv_passed"]


def test_signal_core_needs_two_bars():
    core = SignalCore()
    b1 = _bar(100, 105, 95, 102, start=0)
    assert core.on_bar(b1) is None
    b2 = _bar(102, 120, 100, 118, start=15 * 60_000)
    sig = core.on_bar(b2)
    assert sig is not None
    assert sig.kind == SignalKind.LONG
