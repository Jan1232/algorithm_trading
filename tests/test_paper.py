from bot.core.validator import StabilityValidator
from bot.exchange.paper import PaperBroker
from bot.models import ClosedTrade, Order, OrderType, Side
from bot.orders.killswitch import KillSwitch
from bot.orders.manager import OrderManager
from bot.core.risk import TfDrawdownTracker
from bot.models import Bar, Signal, SignalKind, VoteResult


def test_paper_roundtrip():
    broker = PaperBroker()
    broker.mark_price("BTCUSDT", 100)
    broker.place_order(
        Order(
            order_id="",
            symbol="BTCUSDT",
            side=Side.LONG,
            order_type=OrderType.MARKET,
            qty=1,
            tf_min=15,
            stop_price=95,
        )
    )
    pos = broker.get_position("BTCUSDT", 15)
    assert pos is not None
    # Adverse slippage on buy
    assert pos.entry_price > 100

    broker.mark_price("BTCUSDT", 110)
    broker.place_order(
        Order(
            order_id="",
            symbol="BTCUSDT",
            side=Side.LONG,
            order_type=OrderType.MARKET,
            qty=1,
            reduce_only=True,
            tf_min=15,
        )
    )
    assert broker.get_position("BTCUSDT", 15) is None
    assert len(broker.closed_trades) == 1
    # Net pnl after costs should be less than raw 10
    assert broker.closed_trades[0].pnl < 10
    assert broker.closed_trades[0].costs_usd > 0


def test_stop_limit_triggers():
    broker = PaperBroker()
    broker.mark_price("BTCUSDT", 100)
    broker.place_order(
        Order(
            order_id="1",
            symbol="BTCUSDT",
            side=Side.LONG,
            order_type=OrderType.STOP_LIMIT,
            qty=0.5,
            price=105,
            stop_price=105,
            tf_min=15,
        )
    )
    assert broker.get_position("BTCUSDT", 15) is None
    broker.mark_price("BTCUSDT", 106)
    assert broker.get_position("BTCUSDT", 15) is not None


def test_validator_mo():
    v = StabilityValidator()
    v.add(
        ClosedTrade("BTCUSDT", Side.LONG, 15, 1, 100, 110, 10, 0, 1)
    )
    v.add(
        ClosedTrade("BTCUSDT", Side.LONG, 15, 1, 100, 95, -5, 0, 1)
    )
    r = v.report()
    assert r.n_trades == 2
    assert r.n_wins == 1
    assert r.mo > 0
    assert r.mass_ok


def test_order_manager_flat_closes():
    broker = PaperBroker()
    broker.mark_price("BTCUSDT", 100)
    broker.place_order(
        Order(
            order_id="",
            symbol="BTCUSDT",
            side=Side.LONG,
            order_type=OrderType.MARKET,
            qty=1,
            tf_min=15,
            stop_price=90,
        )
    )
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
        tracker=TfDrawdownTracker(),
        validator=StabilityValidator(),
    )
    bar = Bar("BTCUSDT", 15, 100, 105, 95, 100, 0, 1, 1)
    prev = Bar("BTCUSDT", 15, 100, 110, 90, 105, 0, 1, 1)
    sig = Signal(SignalKind.FLAT, "BTCUSDT", 15, bar, prev)
    om.on_signals("BTCUSDT", 100, [sig], VoteResult(n_flat=1), {15: prev})
    assert broker.get_position("BTCUSDT", 15) is None
    assert om.validator.report().n_trades == 1
