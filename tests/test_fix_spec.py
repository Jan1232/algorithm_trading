"""Window B / FIX SPEC unit tests."""
from __future__ import annotations

from bot.core.allocator import allocate_by_stop_risk
from bot.core.risk import TfDrawdownTracker, trailing_stop_price
from bot.models import Bar, Side, Signal, SignalKind, VoteResult


def test_vote_n_none_not_in_dominant_and_gate():
    # One long vs seven none/flat → FLAT under gated dominant
    v = VoteResult(n_long=1, n_short=0, n_flat=0, n_none=7, min_directional=2, min_margin=2)
    assert v.dominant == SignalKind.FLAT
    assert v.n_directional == 1
    assert v.n_voting == 1
    assert v.n_total == 8

    # L1S0F7 style: one long, seven flat → still FLAT
    v2 = VoteResult(n_long=1, n_short=0, n_flat=7, n_none=0, min_directional=2, min_margin=2)
    assert v2.dominant == SignalKind.FLAT

    # Two shorts with margin 2 → SHORT
    v3 = VoteResult(n_long=0, n_short=2, n_flat=4, n_none=2, min_directional=2, min_margin=2)
    assert v3.dominant == SignalKind.SHORT

    # Two long but only margin 1 vs short → FLAT
    v4 = VoteResult(n_long=2, n_short=1, n_flat=3, n_none=0, min_directional=2, min_margin=2)
    assert v4.dominant == SignalKind.FLAT

    # Three long vs one short → margin 2 → LONG
    v5 = VoteResult(n_long=3, n_short=1, n_flat=2, n_none=0, min_directional=2, min_margin=2)
    assert v5.dominant == SignalKind.LONG


def test_allocator_risk_budget_is_per_trade_not_split():
    tracker = TfDrawdownTracker()
    signals = []
    for tf in (60, 120, 240):
        bar = Bar("ETHUSDT", tf, 2000, 2010, 1990, 2005, 0, 1, 1)
        prev = Bar("ETHUSDT", tf, 2000, 2008, 1995, 2002, 0, 1, 1)
        signals.append(Signal(SignalKind.LONG, "ETHUSDT", tf, bar, prev, k=0.6, delta=5))

    deposit = 1000.0
    per = 0.02
    allocs = allocate_by_stop_risk(
        signals,
        kind=SignalKind.LONG,
        deposit=deposit,
        price=2000,
        tracker=tracker,
        risk_pct=0.10,
        per_trade_risk_pct=per,
        max_leverage_frac=1.0,
    )
    assert len(allocs) == 3
    # risk_budget independent of len(eligible)
    expected = deposit * per
    for a in allocs:
        assert abs(a.risk_usd - expected) < 1e-6 or a.risk_usd <= expected + 1e-9
        assert a.entry_trigger == 2008
        assert a.protect_stop == 1995


def test_trailing_buffer_moves_stop_away_from_extreme():
    assert trailing_stop_price(Side.LONG, 90, 110) == 90
    assert trailing_stop_price(Side.SHORT, 90, 110) == 110
    buffered_long = trailing_stop_price(
        Side.LONG, 90, 110, buffer_frac=0.10, bar_range=20.0
    )
    buffered_short = trailing_stop_price(
        Side.SHORT, 90, 110, buffer_frac=0.10, bar_range=20.0
    )
    assert buffered_long == 88.0  # 90 - 0.1*20
    assert buffered_short == 112.0  # 110 + 0.1*20
    assert buffered_long < 90
    assert buffered_short > 110


def test_reconcile_meta_preserves_already_through():
    """BUG-2: pending_meta already_through must survive reconcile persist path."""
    from bot.orders.manager import OrderManager
    from bot.orders.killswitch import KillSwitch
    from bot.models import Position, Side
    from bot.storage.db import TradeStore
    import tempfile
    from pathlib import Path

    class StubBroker:
        def __init__(self) -> None:
            self._pos = Position("SOLUSDT", Side.LONG, 1.0, 74.5, 60, stop_price=73.0)

        def get_positions(self):
            return [self._pos]

        def get_position(self, symbol, tf_min):
            return self._pos if symbol == "SOLUSDT" else None

        def place_order(self, order):
            return order

        def cancel_all(self, symbol=None):
            return None

    with tempfile.TemporaryDirectory() as td:
        store = TradeStore(Path(td) / "t.db")
        om = OrderManager(
            StubBroker(),
            deposit=100,
            max_drawdown_usd=10,
            tf_risk_pct=0.1,
            kill_switch=KillSwitch(
                max_orders_per_minute=30,
                max_open_positions=20,
                max_daily_loss_pct=0.05,
                deposit=100,
            ),
            store=store,
            mode="demo",
        )
        om._pending_meta[("SOLUSDT", 60)] = {
            "already_through": True,
            "entry_type": "market_through",
            "trigger": 74.0,
            "signal_price": 74.2,
            "entry_reason": "test",
            "risk_usd": 2.0,
            "vote": {"long": 2, "short": 0, "flat": 2, "none": 2},
        }
        om._persist_adopted_position(om.broker.get_positions()[0])
        opens = store.list_open_trades()
        assert len(opens) == 1
        import json

        market = json.loads(opens[0]["market_json"])
        assert market["already_through"] is True
        assert market["entry_type"] == "market_through"
        assert market["slippage_vs_trigger"] == 0.5  # 74.5 - 74.0
        store.close()
