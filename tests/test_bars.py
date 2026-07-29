from bot.core.bars import TickBarBuilder
from bot.models import Tick


def test_builds_ohlc_across_buckets():
    b = TickBarBuilder("BTCUSDT", dt_min=1)
    # bucket 0
    assert b.on_tick(Tick("BTCUSDT", 100, 1, 0)) is None
    assert b.on_tick(Tick("BTCUSDT", 110, 1, 10_000)) is None
    assert b.on_tick(Tick("BTCUSDT", 90, 1, 20_000)) is None
    # next minute bucket → flush
    bar = b.on_tick(Tick("BTCUSDT", 105, 1, 60_000))
    assert bar is not None
    assert bar.open == 100
    assert bar.high == 110
    assert bar.low == 90
    assert bar.close == 90
    assert bar.tick_count == 3
    assert bar.start_ts_ms == 0


def test_flush_open_bar():
    b = TickBarBuilder("ETHUSDT", dt_min=5)
    assert b.on_tick(Tick("ETHUSDT", 50, 1, 1000)) is None
    bar = b.flush()
    assert bar is not None
    assert bar.open == bar.close == 50
