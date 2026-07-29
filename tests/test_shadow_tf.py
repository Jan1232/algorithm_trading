"""Shadow short-TF collectors: record-only, outside policy hash."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.config import Settings, load_settings
from bot.core.bars import TickBarBuilder
from bot.core.costs import CostModel
from bot.core.liquidity import LiquidityFilter
from bot.models import Bar, Tick
from bot.runtime.portfolio import SymbolWorker
from bot.storage.db import TradeStore


def test_shadow_tf_not_in_hash():
    """Main NO-HASH invariant: shadow TF list must not change frozen_policy_hash."""
    base = load_settings()
    h0 = base.frozen_policy_hash()
    assert h0 == "3eddb8d58eff91d6"

    a = Settings(
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
        shadow_timeframes_min=[],
    )
    b = Settings(
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
        shadow_timeframes_min=[1, 5],
    )
    assert a.frozen_policy_hash() == b.frozen_policy_hash() == h0
    assert "shadow" not in str(a.frozen_policy_hash())


def test_shadow_bars_isolated(tmp_path: Path):
    db = tmp_path / "iso.db"
    store = TradeStore(str(db))
    trade_bar = Bar("ETHUSDT", 60, 1, 2, 0.5, 1.5, 0, 60_000, 3)
    shadow_bar = Bar("ETHUSDT", 1, 1, 2, 0.5, 1.5, 0, 60_000, 3)
    store.save_bar(trade_bar)
    store.save_shadow_bar(shadow_bar)
    store.close()

    conn = sqlite3.connect(str(db))
    n_bars = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    n_shadow = conn.execute("SELECT COUNT(*) FROM shadow_bars").fetchone()[0]
    shadow_tfs = [
        r[0]
        for r in conn.execute("SELECT DISTINCT tf_min FROM shadow_bars").fetchall()
    ]
    bar_tfs = [r[0] for r in conn.execute("SELECT DISTINCT tf_min FROM bars").fetchall()]
    conn.close()

    assert n_bars == 1 and n_shadow == 1
    assert 1 in shadow_tfs and 1 not in bar_tfs
    assert 60 in bar_tfs and 60 not in shadow_tfs

    from bot.analysis.momentum_diagnostics import diagnose
    from bot.analysis.monkey import load_bar_series

    conn = sqlite3.connect(str(db))
    series = load_bar_series(conn)
    conn.close()
    assert ( "ETHUSDT", 1) not in series
    assert ("ETHUSDT", 60) in series

    main = diagnose(db, table="bars")
    sh = diagnose(db, table="shadow_bars")
    assert main["n_cells"] == 1
    assert sh["n_cells"] == 1
    assert main["cells"][0]["tf_min"] == 60
    assert sh["cells"][0]["tf_min"] == 1


def test_shadow_builder_no_orders(tmp_path: Path):
    db = tmp_path / "sw.db"
    store = TradeStore(str(db))
    settings = Settings(
        symbols=["ETHUSDT"],
        timeframes_explicit=[60],  # trading TF far from 1m closes in this span
        shadow_timeframes_min=[1, 5],
        vote_min_directional=2,
        vote_min_margin=2,
    )
    om = MagicMock()
    om.broker = MagicMock()
    worker = SymbolWorker(
        "ETHUSDT",
        settings,
        om,
        LiquidityFilter(0.0),
        store,
    )

    base = 1_700_000_000_000
    price = 2000.0
    # ~12 minutes of ticks every 5s → several 1m and some 5m shadow bars
    for i in range(150):
        ts = base + i * 5_000
        price *= 1.0 + ((i % 7) - 3) * 0.00005
        worker.on_tick(Tick("ETHUSDT", price, 1.0, ts))

    store.close()
    conn = sqlite3.connect(str(db))
    shadow_n = conn.execute("SELECT COUNT(*) FROM shadow_bars").fetchone()[0]
    bars_n = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    shadow_tfs = {
        r[0] for r in conn.execute("SELECT DISTINCT tf_min FROM shadow_bars")
    }
    conn.close()

    assert shadow_n > 0
    assert shadow_tfs <= {1, 5}
    # Trading 60m should not have closed yet in 12 minutes
    assert bars_n == 0
    # No trading signals / orders from shadow path
    om.on_signals.assert_not_called()
    # mark/on_price may run; that's fine — no on_signals for tf 1/5


def test_diagnose_reads_shadow_table(tmp_path: Path):
    from bot.analysis.momentum_diagnostics import diagnose

    db = tmp_path / "d.db"
    store = TradeStore(str(db))
    # enough closes for a cell
    for i in range(20):
        store.save_shadow_bar(
            Bar("BTCUSDT", 1, 100 + i, 101 + i, 99 + i, 100.5 + i, i * 60_000, (i + 1) * 60_000, 2)
        )
        store.save_bar(
            Bar("BTCUSDT", 60, 100 + i, 101 + i, 99 + i, 100.5 + i, i * 3_600_000, (i + 1) * 3_600_000, 2)
        )
    store.close()

    sh = diagnose(db, table="shadow_bars")
    main = diagnose(db, table="bars")
    assert sh["table"] == "shadow_bars"
    assert sh["cells"][0]["tf_min"] == 1
    assert main["cells"][0]["tf_min"] == 60

    with pytest.raises(ValueError):
        diagnose(db, table="trades")


def test_shadow_costs_viability_ratio(tmp_path: Path):
    from bot.analysis.momentum_diagnostics import shadow_costs_viability

    db = tmp_path / "c.db"
    store = TradeStore(str(db))
    # Large body moves → viable; tiny bodies → not
    for i in range(30):
        store.save_shadow_bar(
            Bar(
                "ETHUSDT",
                1,
                100.0,
                102.0,
                99.0,
                101.0,  # 100 bps body
                i * 60_000,
                (i + 1) * 60_000,
                5,
            )
        )
    store.close()
    costs = CostModel(taker_fee_bps=5.5, slippage_bps=5.0, funding_bps_per_8h=1.0)
    # rt ≈ 2*5.5 + 2*5 = 21 bps (+tiny funding) → ratio ~100/21 > 2
    out = shadow_costs_viability(db, costs)
    assert out["rows"]
    assert out["rows"][0]["viable"] is True
    assert out["rows"][0]["ratio_body_over_cost"] > 2.0
