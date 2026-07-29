from pathlib import Path

from bot.exchange.instruments import InstrumentRegistry, InstrumentSpec
from bot.experiment import load_or_create_experiment, promote_to_window_b
from bot.orders.killswitch import KillSwitch
from bot.orders.manager import OrderManager
from bot.exchange.paper import PaperBroker
from bot.models import Bar, Order, OrderType, Side, Signal, SignalKind, VoteResult


def test_qty_rounding():
    spec = InstrumentSpec("BTCUSDT", 0.001, 0.001, 0.1)
    assert spec.round_qty(0.0019) == 0.001
    assert spec.round_qty(0.0004) == 0.0
    assert abs(spec.round_price(100.04) - 100.0) < 1e-9 or abs(spec.round_price(100.06) - 100.1) < 1e-9


def test_registry_fallback():
    reg = InstrumentRegistry()
    btc = reg.get("BTCUSDT")
    assert btc.qty_step == 0.001


def test_experiment_freeze(tmp_path: Path):
    state = load_or_create_experiment(tmp_path, policy_hash="abc123", strategy_label="test")
    assert state.window == "A"
    again = load_or_create_experiment(tmp_path, policy_hash="abc123", strategy_label="test")
    assert again.policy_hash == "abc123"
    promote_to_window_b(tmp_path, again)
    assert again.window == "B"


def test_book_close_exit_on_close_turn():
    broker = PaperBroker()
    broker.mark_price("BTCUSDT", 100)
    # open long manually via market
    broker.place_order(
        Order(
            order_id="",
            symbol="BTCUSDT",
            side=Side.LONG,
            order_type=OrderType.MARKET,
            qty=0.01,
            tf_min=15,
            stop_price=90,
        )
    )
    assert broker.get_position("BTCUSDT", 15) is not None

    ks = KillSwitch(
        max_orders_per_minute=30,
        max_open_positions=20,
        max_daily_loss_pct=0.05,
        deposit=10_000,
    )
    om = OrderManager(
        broker,
        deposit=10_000,
        max_drawdown_usd=1000,
        tf_risk_pct=0.1,
        kill_switch=ks,
        exit_mode="book_close",
    )
    prev = Bar("BTCUSDT", 15, 100, 110, 95, 108, 0, 1, 1)
    cur = Bar("BTCUSDT", 15, 108, 109, 100, 101, 1, 2, 1)  # close fell 108->101
    sig = Signal(SignalKind.LONG, "BTCUSDT", 15, cur, prev, k=0.6, delta=1)
    # vote still long but book exit should close on close turn
    vote = VoteResult(n_long=1, signals=[sig])
    om.on_signals("BTCUSDT", 101, [sig], vote, {15: prev})
    assert broker.get_position("BTCUSDT", 15) is None
    assert broker.closed_trades[-1].pnl is not None
