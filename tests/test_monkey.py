"""Monkey test unit tests (Davey-style random-strategy benchmark)."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from bot.analysis.monkey import (
    _make_closed,
    _matches_stats,
    evaluate_monkey_gate,
    extract_trade_stats,
    mo_from_closed,
    run_monkey_test,
    simulate_monkey_run,
)
from bot.core.costs import CostModel
from bot.experiment import PassCriteria, evaluate_pass
from bot.models import Side


def _seed_db(path: Path, *, n_bars: int = 400, n_trades: int = 40, edge: float = 0.0) -> None:
    """Synthetic bars + closed trades with optional edge on exits."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            tf_min INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            start_ts_ms INTEGER NOT NULL,
            end_ts_ms INTEGER NOT NULL,
            tick_count INTEGER NOT NULL,
            UNIQUE(symbol, tf_min, start_ts_ms)
        );
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            tf_min INTEGER NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            pnl REAL,
            s REAL,
            opened_ts_ms INTEGER NOT NULL,
            closed_ts_ms INTEGER,
            entry_reason TEXT NOT NULL,
            exit_reason TEXT,
            market_json TEXT NOT NULL,
            signal_id INTEGER,
            status TEXT NOT NULL DEFAULT 'open',
            mode TEXT,
            fees_usd REAL,
            slippage_usd REAL,
            funding_usd REAL,
            policy_hash TEXT
        );
        """
    )
    symbol = "ETHUSDT"
    tf = 60
    tf_ms = tf * 60_000
    price = 2000.0
    for i in range(n_bars):
        price = price * (1.0 + 0.0005 + ((i % 7) - 3) * 0.0002)
        start = 1_700_000_000_000 + i * tf_ms
        conn.execute(
            """
            INSERT INTO bars(symbol, tf_min, open, high, low, close,
                             start_ts_ms, end_ts_ms, tick_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, tf, price, price * 1.001, price * 0.999, price, start, start + tf_ms, 10),
        )

    costs = CostModel()
    closes = [
        r[0]
        for r in conn.execute(
            "SELECT close FROM bars WHERE tf_min=? ORDER BY start_ts_ms", (tf,)
        )
    ]
    starts = [
        r[0]
        for r in conn.execute(
            "SELECT start_ts_ms FROM bars WHERE tf_min=? ORDER BY start_ts_ms", (tf,)
        )
    ]
    ends = [
        r[0]
        for r in conn.execute(
            "SELECT end_ts_ms FROM bars WHERE tf_min=? ORDER BY start_ts_ms", (tf,)
        )
    ]
    hold = 3
    step = max(1, (n_bars - hold - 1) // n_trades)
    for k in range(n_trades):
        i = 1 + k * step
        if i + hold >= n_bars:
            break
        side = Side.LONG if (k % 5) != 0 else Side.SHORT  # ~80% long
        entry = closes[i]
        exit_px = closes[i + hold]
        if edge and side == Side.LONG:
            exit_px = entry * (1.0 + edge)
        elif edge and side == Side.SHORT:
            exit_px = entry * (1.0 - edge)
        t = _make_closed(
            symbol=symbol,
            tf_min=tf,
            side=side,
            qty=0.1,
            entry_price=entry,
            exit_price=exit_px,
            opened_ts_ms=starts[i],
            closed_ts_ms=ends[i + hold],
            costs=costs,
        )
        conn.execute(
            """
            INSERT INTO trades(
                symbol, tf_min, side, qty, entry_price, exit_price, pnl, s,
                opened_ts_ms, closed_ts_ms, entry_reason, exit_reason, market_json,
                status, fees_usd, slippage_usd, funding_usd, policy_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?, ?)
            """,
            (
                t.symbol,
                t.tf_min,
                t.side.value,
                t.qty,
                t.entry_price,
                t.exit_price,
                t.pnl,
                t.s,
                t.opened_ts_ms,
                t.closed_ts_ms,
                "test",
                "test",
                "{}",
                t.fees_usd,
                t.slippage_usd,
                t.funding_usd,
                "testhash",
            ),
        )
    conn.commit()
    conn.close()


def test_costs_applied_on_monkey_trades():
    costs = CostModel(taker_fee_bps=5.5, slippage_bps=5.0, funding_bps_per_8h=1.0)
    assert mo_from_closed([]) == 0.0

    t = _make_closed(
        symbol="ETHUSDT",
        tf_min=60,
        side=Side.LONG,
        qty=1.0,
        entry_price=100.0,
        exit_price=100.0,
        opened_ts_ms=0,
        closed_ts_ms=8 * 3_600_000,
        costs=costs,
    )
    assert t.fees_usd > 0
    assert t.slippage_usd > 0
    assert t.funding_usd > 0
    assert t.costs_usd > 0
    assert t.s < 0


def test_consistency_trade_count_and_long_frac(tmp_path: Path):
    db = tmp_path / "m.db"
    _seed_db(db, n_bars=500, n_trades=50)
    conn = sqlite3.connect(str(db))
    stats = extract_trade_stats(conn, policy_hash="testhash")
    from bot.analysis.monkey import _attach_entry_indices, load_bar_series, load_baseline_trades

    baseline = load_baseline_trades(conn, policy_hash="testhash")
    series = load_bar_series(conn)
    _attach_entry_indices(baseline, series)
    conn.close()

    costs = CostModel()
    for mode in ("entry", "exit", "both"):
        ok = 0
        for i in range(20):
            rng = random.Random(100 + i)
            trades = simulate_monkey_run(
                rng,
                mode=mode,  # type: ignore[arg-type]
                stats=stats,
                baseline=baseline,
                series=series,
                costs=costs,
            )
            if _matches_stats(trades, stats):
                ok += 1
        assert ok >= 15, f"mode={mode} only {ok}/20 matched ±10%"


def test_determinism_same_seed(tmp_path: Path):
    db = tmp_path / "m.db"
    _seed_db(db, n_bars=300, n_trades=30)
    a = run_monkey_test(
        db,
        policy_hash="testhash",
        modes=("both",),
        n_runs=40,
        seed=42,
        beat_threshold=0.90,
    )
    b = run_monkey_test(
        db,
        policy_hash="testhash",
        modes=("both",),
        n_runs=40,
        seed=42,
        beat_threshold=0.90,
    )
    assert len(a) == 1 and len(b) == 1
    assert a[0].pct_runs_mo_worse == b[0].pct_runs_mo_worse
    assert a[0].pct_runs_maxdd_worse == b[0].pct_runs_maxdd_worse
    assert a[0].mo_distribution == b[0].mo_distribution
    assert a[0].passed == b[0].passed


def test_gate_insufficient_data_does_not_block(tmp_path: Path):
    db = tmp_path / "m.db"
    _seed_db(db, n_bars=100, n_trades=10)
    verdict = evaluate_monkey_gate(
        db,
        trades_closed=10,
        min_closed_trades=200,
        require_monkey_pass=True,
        monkey_beat_threshold=0.90,
        monkey_runs=10,
        monkey_seed=42,
        policy_hash="testhash",
    )
    assert verdict.status == "insufficient_data"
    assert verdict.blocks is False

    ev = evaluate_pass(
        trades_closed=10,
        mo=0.5,
        tf_baskets_positive_mo_pct=1.0,
        max_drawdown_pct=0.01,
        echelon2_block_rate=0.1,
        criteria=PassCriteria(min_closed_trades=200, require_monkey_pass=True),
        monkey_status="insufficient_data",
    )
    assert ev.passed is False
    assert "monkey gate FAIL" not in " ".join(ev.reasons)
    assert ev.monkey_status == "insufficient_data"


def test_sanity_strong_edge_pass_noise_fail(tmp_path: Path):
    db_edge = tmp_path / "edge.db"
    _seed_db(db_edge, n_bars=500, n_trades=40, edge=0.02)
    edge_results = run_monkey_test(
        db_edge,
        policy_hash="testhash",
        modes=("entry", "exit", "both"),
        n_runs=80,
        seed=1,
        beat_threshold=0.70,
    )
    assert all(r.passed for r in edge_results), [
        (r.mode, r.pct_runs_mo_worse, r.pct_runs_maxdd_worse) for r in edge_results
    ]

    db_noise = tmp_path / "noise.db"
    _seed_db(db_noise, n_bars=500, n_trades=40, edge=0.0)
    noise = run_monkey_test(
        db_noise,
        policy_hash="testhash",
        modes=("both",),
        n_runs=120,
        seed=2,
        beat_threshold=0.90,
    )
    assert len(noise) == 1
    assert noise[0].passed is False or noise[0].pct_runs_mo_worse < 0.90


def test_pass_criteria_monkey_fields_defaults():
    c = PassCriteria()
    assert c.require_monkey_pass is True
    assert c.monkey_beat_threshold == 0.90
    assert c.monkey_runs == 2000
    assert c.monkey_seed == 42


def test_seed_reproducible_across_pythonhashseed(tmp_path: Path):
    """
    A-1 regression: logged seed must reproduce across processes even when
    PYTHONHASHSEED differs. Old code used hash(mode) and failed this.
    """
    import json
    import os
    import subprocess
    import sys

    db = tmp_path / "hashseed.db"
    _seed_db(db, n_bars=250, n_trades=25)
    script = (
        "import json\n"
        "from bot.analysis.monkey import run_monkey_test\n"
        f"results = run_monkey_test({str(db)!r}, policy_hash='testhash', "
        "modes=('entry','exit','both'), n_runs=20, seed=42, beat_threshold=0.90)\n"
        "print(json.dumps(["
        "{'mode': r.mode, 'mo': r.pct_runs_mo_worse, 'dd': r.pct_runs_maxdd_worse, "
        "'dist': r.mo_distribution, 'passed': r.passed} for r in results]))\n"
    )
    root = Path(__file__).resolve().parent.parent
    outs = []
    for hs in ("0", "1"):
        env = {**os.environ, "PYTHONHASHSEED": hs, "PYTHONPATH": str(root)}
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(root),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        outs.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    assert outs[0] == outs[1]


def test_frozen_policy_hash_uses_passcriteria_monkey_defaults():
    from bot.config import load_settings
    from bot.experiment import (
        ECHELON2_BLOCK_RATE_GATE_ENABLED_DEFAULT,
        MONKEY_BEAT_THRESHOLD_DEFAULT,
        MONKEY_RUNS_DEFAULT,
        MONKEY_SEED_DEFAULT,
        REQUIRE_MONKEY_PASS_DEFAULT,
    )

    assert REQUIRE_MONKEY_PASS_DEFAULT is True
    assert MONKEY_BEAT_THRESHOLD_DEFAULT == 0.90
    assert MONKEY_RUNS_DEFAULT == 2000
    assert MONKEY_SEED_DEFAULT == 42
    assert ECHELON2_BLOCK_RATE_GATE_ENABLED_DEFAULT is False
    # Prefreeze econ pack (deposit_fallback=1000, funding entry notional, …)
    assert load_settings().frozen_policy_hash() == "3eddb8d58eff91d6"
