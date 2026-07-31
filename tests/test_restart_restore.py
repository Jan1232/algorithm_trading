"""Restart state recovery: SignalCore._prev from DB + optional kline partial seed."""

from __future__ import annotations

from pathlib import Path

from bot.config import Settings, load_settings
from bot.core.bars import TickBarBuilder
from bot.core.mtf import MultiTFEngine
from bot.core.signals import SignalCore, evaluate_signal
from bot.exchange.market_data import tf_to_bybit_interval
from bot.models import Bar
from bot.storage.db import TradeStore


def _bar(o, h, l, c, *, tf=60, start=0, symbol="BTCUSDT") -> Bar:
    return Bar(
        symbol=symbol,
        tf_min=tf,
        open=o,
        high=h,
        low=l,
        close=c,
        start_ts_ms=start,
        end_ts_ms=start + tf * 60_000,
        tick_count=5,
    )


def test_last_bar_storage(tmp_path: Path):
    store = TradeStore(tmp_path / "t.db")
    older = _bar(100, 110, 90, 105, start=0)
    newer = _bar(105, 120, 100, 118, start=60 * 60_000)
    store.save_bar(older)
    store.save_bar(newer)
    last = store.last_bar("BTCUSDT", 60)
    assert last is not None
    assert last.start_ts_ms == newer.start_ts_ms
    assert last.close == 118
    assert store.last_bar("BTCUSDT", 240) is None
    store.close()


def test_seed_prev_restores_signal(tmp_path: Path):
    """After restart+restore, first closed bar uses DB prev — same as no-restart path."""
    prev = _bar(100, 105, 95, 102, start=0)
    cur = _bar(102, 120, 100, 118, start=60 * 60_000)
    expected = evaluate_signal(cur, prev)

    store = TradeStore(tmp_path / "t.db")
    store.save_bar(prev)

    # Continuous process (no restart): seed via on_bar then evaluate
    core_live = SignalCore()
    assert core_live.on_bar(prev) is None
    live_sig = core_live.on_bar(cur)
    assert live_sig is not None
    assert live_sig.kind == expected.kind

    # Restart: fresh engine + restore from DB
    eng = MultiTFEngine("BTCUSDT", [60])
    stats = eng.restore(store)
    assert stats["prev_seeded"] == 1
    assert eng.last_bars[60].start_ts_ms == prev.start_ts_ms

    restored = eng.cores[60].on_bar(cur)
    assert restored is not None
    assert restored.kind == expected.kind
    assert restored.kind == live_sig.kind
    assert restored.checks["prev"]["H"] == prev.high
    assert restored.checks["prev"]["L"] == prev.low
    store.close()


def test_restore_no_bars_safe(tmp_path: Path):
    store = TradeStore(tmp_path / "empty.db")
    eng = MultiTFEngine("ETHUSDT", [60, 120, 240])
    stats = eng.restore(store)
    assert stats["prev_seeded"] == 0
    assert eng.last_bars == {}
    assert eng.cores[60]._prev == {}
    store.close()


def test_seed_partial_bucket_guard():
    b = TickBarBuilder("BTCUSDT", dt_min=60)
    dt = 60 * 60_000
    now = 3 * dt + 15_000  # inside bucket start=3*dt
    past = 2 * dt

    assert b.seed_partial(
        open_=1, high=2, low=0.5, close=1.5, start_ts_ms=past, now_ms=now
    ) is False
    assert b._start_ts_ms is None

    assert b.seed_partial(
        open_=10, high=12, low=9, close=11, start_ts_ms=3 * dt, now_ms=now
    ) is True
    assert b._start_ts_ms == 3 * dt
    assert b._open == 10

    # Live bar must not be overwritten
    assert b.seed_partial(
        open_=99, high=99, low=99, close=99, start_ts_ms=3 * dt, now_ms=now
    ) is False
    assert b._open == 10


def test_restore_partial_via_fetch(tmp_path: Path):
    store = TradeStore(tmp_path / "t.db")
    eng = MultiTFEngine("BTCUSDT", [60, 480])  # 480 has no native kline
    now = 60 * 60_000 + 1_000
    candle_60 = {
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 103.0,
        "start_ts_ms": 60 * 60_000,
    }

    def fetch(sym: str, tf: int):
        if tf == 60:
            return candle_60
        return None  # 480 unsupported

    stats = eng.restore(store, fetch_kline=fetch, now_ms=now)
    assert stats["partial_seeded"] == 1
    assert stats["partial_skipped"] == 1
    assert eng.builders[60]._start_ts_ms == candle_60["start_ts_ms"]
    assert eng.builders[480]._start_ts_ms is None
    store.close()


def test_restore_not_in_hash():
    base = load_settings()
    h0 = base.frozen_policy_hash()
    assert h0 == "b38a2daf61e58795"

    off = Settings(
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
        restore_partial_from_kline=False,
    )
    on = Settings(
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
        restore_partial_from_kline=True,
    )
    assert off.frozen_policy_hash() == on.frozen_policy_hash() == h0


def test_bybit_interval_map():
    assert tf_to_bybit_interval(60) == "60"
    assert tf_to_bybit_interval(120) == "120"
    assert tf_to_bybit_interval(240) == "240"
    assert tf_to_bybit_interval(1440) == "D"
    assert tf_to_bybit_interval(480) is None
    assert tf_to_bybit_interval(960) is None
