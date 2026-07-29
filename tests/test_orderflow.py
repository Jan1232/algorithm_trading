"""Order-flow collectors: record-only, outside policy hash."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from bot.analysis.orderflow import OrderFlowBuilder
from bot.config import Settings, load_settings
from bot.core.bars import TickBarBuilder, bucket_start
from bot.core.liquidity import LiquidityFilter
from bot.models import Tick
from bot.runtime.portfolio import SymbolWorker
from bot.storage.db import TradeStore


def test_orderflow_not_in_hash():
    base = load_settings()
    h0 = base.frozen_policy_hash()
    assert h0 == "3eddb8d58eff91d6"

    common = dict(
        deposit_usd=base.deposit_usd,
        max_drawdown_pct=base.max_drawdown_pct,
        tf_risk_pct=base.tf_risk_pct,
        per_trade_risk_pct=base.per_trade_risk_pct,
        max_leverage_frac=base.max_leverage_frac,
        trailing_buffer_frac=base.trailing_buffer_frac,
        vote_min_directional=base.vote_min_directional,
        vote_min_margin=base.vote_min_margin,
        one_position_per_symbol=base.one_position_per_symbol,
        symbols=list(base.symbols),
        timeframes_explicit=list(base.timeframes_min),
        costs=base.costs,
        strategy_label=base.strategy_label,
        exit_mode=base.exit_mode,
        shadow_timeframes_min=list(base.shadow_timeframes_min),
    )
    off = Settings(**common, orderflow_collect=False, orderflow_tf_min=[])
    on = Settings(
        **common,
        orderflow_collect=True,
        orderflow_tf_min=[60, 240],
        orderflow_price_bucket_bps=2.0,
    )
    assert off.frozen_policy_hash() == on.frozen_policy_hash() == h0


def test_tick_side_optional_backcompat():
    t = Tick("ETHUSDT", 100.0, 1.0, 1_000)
    assert t.aggressor is None
    t2 = Tick("ETHUSDT", 100.0, 1.0, 1_000, aggressor="Buy")
    assert t2.aggressor == "Buy"


def test_orderflow_delta():
    b = OrderFlowBuilder("ETHUSDT", 1, price_bucket_bps=10.0)
    base = 1_700_000_000_000
    # same 1m bucket
    assert b.on_tick(Tick("ETHUSDT", 100.0, 3.0, base + 1_000, aggressor="Buy")) is None
    assert b.on_tick(Tick("ETHUSDT", 100.0, 1.0, base + 2_000, aggressor="Sell")) is None
    assert b.on_tick(Tick("ETHUSDT", 100.0, 0.5, base + 3_000, aggressor=None)) is None
    closed = b.on_tick(Tick("ETHUSDT", 100.0, 1.0, base + 60_000, aggressor="Buy"))
    assert closed is not None
    assert closed.buy_vol == 3.0
    assert closed.sell_vol == 1.0
    assert closed.unknown_vol == 0.5
    assert closed.delta == 2.0


def test_orderflow_footprint_buckets():
    b = OrderFlowBuilder("BTCUSDT", 1, price_bucket_bps=100.0)  # 1% buckets
    base = 1_700_000_000_000
    # ~1% step at 100 → step=1
    b.on_tick(Tick("BTCUSDT", 100.0, 2.0, base + 100, aggressor="Buy"))
    b.on_tick(Tick("BTCUSDT", 100.4, 1.0, base + 200, aggressor="Buy"))  # same ~100 bucket
    b.on_tick(Tick("BTCUSDT", 102.0, 3.0, base + 300, aggressor="Sell"))
    closed = b.on_tick(Tick("BTCUSDT", 100.0, 0.1, base + 60_000, aggressor="Buy"))
    assert closed is not None
    assert len(closed.footprint) >= 2
    rows = closed.footprint_rows()
    assert all(r["tf_min"] == 1 for r in rows)
    buy_total = sum(r["buy_vol"] for r in rows)
    sell_total = sum(r["sell_vol"] for r in rows)
    assert abs(buy_total - 3.0) < 1e-9
    assert abs(sell_total - 3.0) < 1e-9


def test_orderflow_no_orders(tmp_path: Path):
    db = tmp_path / "of.db"
    store = TradeStore(str(db))
    settings = Settings(
        symbols=["ETHUSDT"],
        timeframes_explicit=[1440],  # day TF — won't close in short span
        orderflow_collect=True,
        orderflow_tf_min=[1],
        orderflow_price_bucket_bps=1.0,
        shadow_timeframes_min=[],
    )
    om = MagicMock()
    om.broker = MagicMock()
    worker = SymbolWorker("ETHUSDT", settings, om, LiquidityFilter(0.0), store)

    base = 1_700_000_000_000
    price = 2000.0
    for i in range(80):
        ts = base + i * 5_000
        price *= 1.00001
        worker.on_tick(
            Tick("ETHUSDT", price, 0.2, ts, aggressor="Buy" if i % 2 == 0 else "Sell")
        )
    store.close()

    conn = sqlite3.connect(str(db))
    of_n = conn.execute("SELECT COUNT(*) FROM orderflow_bars").fetchone()[0]
    fp_n = conn.execute("SELECT COUNT(*) FROM footprint").fetchone()[0]
    bars_n = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    conn.close()

    assert of_n > 0
    assert fp_n > 0
    assert bars_n == 0
    om.on_signals.assert_not_called()


def test_orderflow_bucket_time_matches_bar():
    symbol = "ETHUSDT"
    tf = 5
    ohlc = TickBarBuilder(symbol, tf)
    of = OrderFlowBuilder(symbol, tf, price_bucket_bps=1.0)
    base = 1_700_000_000_000
    # force aligned to bucket
    base = bucket_start(base, tf * 60_000)

    closed_ohlc = None
    closed_of = None
    for i in range(70):
        ts = base + i * 5_000
        tick = Tick(symbol, 100.0 + i * 0.01, 1.0, ts, aggressor="Buy")
        c1 = ohlc.on_tick(tick)
        c2 = of.on_tick(tick)
        if c1 is not None:
            closed_ohlc = c1
        if c2 is not None:
            closed_of = c2

    assert closed_ohlc is not None and closed_of is not None
    assert closed_ohlc.start_ts_ms == closed_of.start_ts_ms
    assert closed_ohlc.start_ts_ms == bucket_start(closed_ohlc.start_ts_ms, tf * 60_000)
