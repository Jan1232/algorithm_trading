"""Guard: bars persisted to SQLite match TickBarBuilder bucket boundaries bit-for-bit."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.core.bars import TickBarBuilder
from bot.models import Tick
from bot.storage.db import TradeStore


def test_stored_bars_match_tick_bar_builder_buckets(tmp_path: Path):
    db = tmp_path / "bars.db"
    store = TradeStore(str(db))
    symbol = "ETHUSDT"
    tf = 5  # 5m so short synthetic ticks close several bars
    tf_ms = tf * 60_000
    builder = TickBarBuilder(symbol, tf)

    base = 1_700_000_000_000
    price = 2000.0
    closed_bars = []
    # 40 minutes of ticks every 5s → several 5m bars
    for i in range(480):
        ts = base + i * 5_000
        price = price * (1.0 + ((i % 11) - 5) * 0.0001)
        closed = builder.on_tick(Tick(symbol, price, 1.0, ts))
        if closed is not None:
            store.save_bar(closed)
            closed_bars.append(closed)
    store.close()

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT start_ts_ms, end_ts_ms, open, high, low, close, tick_count "
        "FROM bars WHERE symbol=? AND tf_min=? ORDER BY start_ts_ms",
        (symbol, tf),
    ).fetchall()
    conn.close()

    assert len(rows) == len(closed_bars) > 0
    for row, bar in zip(rows, closed_bars):
        assert row[0] == bar.start_ts_ms
        assert row[1] == bar.end_ts_ms
        assert row[2] == bar.open
        assert row[3] == bar.high
        assert row[4] == bar.low
        assert row[5] == bar.close
        assert row[6] == bar.tick_count
        assert row[0] == (row[0] // tf_ms) * tf_ms


def test_bucket_start_public_contract():
    b = TickBarBuilder("BTCUSDT", 60)
    tf_ms = 60 * 60_000
    ts = 1_700_000_123_456
    assert b._bucket_start(ts) == (ts // tf_ms) * tf_ms
