from bot.core.risk import (
    TfDrawdownTracker,
    echelon2_allows_new,
    potential_loss,
    stop_hit,
    trailing_stop_price,
)
from bot.models import Position, Side
from bot.orders.killswitch import KillSwitch


def test_trailing_stop_and_hit():
    assert trailing_stop_price(Side.LONG, 90, 110) == 90
    assert trailing_stop_price(Side.SHORT, 90, 110) == 110
    pos = Position("BTCUSDT", Side.LONG, 1, 100, 15, stop_price=90)
    assert stop_hit(pos, 89)
    assert not stop_hit(pos, 91)
    assert trailing_stop_price(Side.LONG, 90, 110, buffer_frac=0.1, bar_range=20) == 88


def test_echelon2_blocks_when_risk_exhausted():
    positions = [
        Position("BTCUSDT", Side.LONG, 10, 100, 15, stop_price=90),  # loss -100
        Position("ETHUSDT", Side.SHORT, 5, 50, 15, stop_price=60),  # loss -50
    ]
    assert abs(potential_loss(positions[0]) - (-100)) < 1e-9
    assert echelon2_allows_new(positions, max_drawdown_usd=200)
    assert not echelon2_allows_new(positions, max_drawdown_usd=100)


def test_tf_drawdown_tracker():
    t = TfDrawdownTracker()
    t.record_pnl("BTCUSDT", 15, 50)
    t.record_pnl("BTCUSDT", 15, -30)
    # peak 50, equity 20 → drawdown 30
    assert abs(t.drawdown("BTCUSDT", 15) - 30) < 1e-9
    t.record_pnl("BTCUSDT", 15, -40)
    # peak 50, equity -20 → drawdown 70
    assert abs(t.drawdown("BTCUSDT", 15) - 70) < 1e-9


def test_kill_switch_rate_limit():
    ks = KillSwitch(
        max_orders_per_minute=3,
        max_open_positions=10,
        max_daily_loss_pct=0.05,
        deposit=10_000,
    )
    assert ks.allow_new_order(0)
    ks.record_order()
    ks.record_order()
    ks.record_order()
    assert not ks.allow_new_order(0)
    assert ks.state.halted
