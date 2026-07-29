"""
Monkey test — random-strategy benchmark (Kevin Davey, ch. 12).

Offline analyser over recorded ``bars`` / ``trades``. Does not change bot
trading behaviour or ``policy_hash`` by itself. The PassCriteria monkey gate
is a separate experiment-protocol change (window B pack).
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from bot.core.costs import CostModel
from bot.core.validator import StabilityValidator
from bot.models import ClosedTrade, Side

MonkeyMode = Literal["entry", "exit", "both"]
ALL_MODES: tuple[MonkeyMode, ...] = ("entry", "exit", "both")
# Deterministic per-mode salt — NEVER use hash(mode); PYTHONHASHSEED breaks
# cross-process reproducibility of a logged seed.
_MODE_SALT: dict[str, int] = {"entry": 1, "exit": 2, "both": 3}

# Regenerate monkey runs outside this relative band.
_MATCH_TOL = 0.10
_MAX_RESAMPLE = 40


@dataclass(frozen=True)
class TradeStats:
    n_trades: int
    long_frac: float
    hold_bars_mean: float
    hold_bars_by_tf: dict[int, float]
    baseline_mo: float
    baseline_maxdd: float
    by_tf: dict[int, int]
    mean_qty: float = 1.0
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class MonkeyResult:
    mode: str
    n_runs: int
    baseline_mo: float
    baseline_maxdd: float
    pct_runs_mo_worse: float
    pct_runs_maxdd_worse: float
    beat_threshold: float
    passed: bool
    mo_distribution: dict[str, float]
    seed: int = 0
    n_trades_baseline: int = 0


@dataclass(frozen=True)
class MonkeyGateVerdict:
    """
    status:
      - pass / fail — decisive
      - insufficient_data — trades_closed < min_closed_trades (does NOT block)
      - skipped — require_monkey_pass=False
    """

    status: str
    results: tuple[MonkeyResult, ...] = ()
    reason: str = ""
    seed: int = 0

    @property
    def blocks(self) -> bool:
        return self.status == "fail"


@dataclass
class _BaselineTrade:
    symbol: str
    tf_min: int
    side: Side
    qty: float
    entry_price: float
    exit_price: float
    opened_ts_ms: int
    closed_ts_ms: int
    fees_usd: float
    slippage_usd: float
    funding_usd: float
    entry_bar_idx: int = -1
    hold_bars: int = 1


@dataclass
class _BarSeries:
    symbol: str
    tf_min: int
    closes: list[float]
    start_ts: list[int]
    end_ts: list[int]

    def __len__(self) -> int:
        return len(self.closes)


def mo_from_closed(trades: Sequence[ClosedTrade]) -> float:
    """Same Mo formula as ``report.py`` / ``StabilityValidator``."""
    v = StabilityValidator()
    for t in trades:
        v.add(t)
    return v.report().mo


def max_drawdown_from_pnls(pnls: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _closed_trade_pnl(t: ClosedTrade) -> float:
    if t.side == Side.LONG:
        gross = (t.exit_price - t.entry_price) * t.qty
    else:
        gross = (t.entry_price - t.exit_price) * t.qty
    return gross - t.costs_usd


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _hold_bars(opened_ts_ms: int, closed_ts_ms: int, tf_min: int) -> float:
    if tf_min <= 0 or closed_ts_ms <= opened_ts_ms:
        return 1.0
    return max(1.0, (closed_ts_ms - opened_ts_ms) / (tf_min * 60_000.0))


def load_baseline_trades(
    conn: sqlite3.Connection,
    *,
    policy_hash: Optional[str] = None,
) -> list[_BaselineTrade]:
    sql = """
        SELECT symbol, tf_min, side, qty, entry_price, exit_price, pnl, s,
               opened_ts_ms, closed_ts_ms,
               COALESCE(fees_usd,0), COALESCE(slippage_usd,0), COALESCE(funding_usd,0),
               COALESCE(exit_reason,'')
        FROM trades
        WHERE status='closed'
    """
    params: list[Any] = []
    if policy_hash:
        sql += " AND policy_hash = ?"
        params.append(policy_hash)
    sql += " ORDER BY closed_ts_ms ASC, id ASC"
    out: list[_BaselineTrade] = []
    for r in conn.execute(sql, params):
        if r[5] is None or r[13] == "sync_orphan":
            continue
        side = Side.LONG if r[2] == "long" else Side.SHORT
        out.append(
            _BaselineTrade(
                symbol=r[0],
                tf_min=int(r[1]),
                side=side,
                qty=float(r[3]),
                entry_price=float(r[4]),
                exit_price=float(r[5]),
                opened_ts_ms=int(r[8] or 0),
                closed_ts_ms=int(r[9] or 0),
                fees_usd=float(r[10] or 0),
                slippage_usd=float(r[11] or 0),
                funding_usd=float(r[12] or 0),
            )
        )
    return out


def load_bar_series(conn: sqlite3.Connection) -> dict[tuple[str, int], _BarSeries]:
    series: dict[tuple[str, int], _BarSeries] = {}
    for r in conn.execute(
        """
        SELECT symbol, tf_min, close, start_ts_ms, end_ts_ms
        FROM bars
        ORDER BY symbol, tf_min, start_ts_ms ASC
        """
    ):
        key = (r[0], int(r[1]))
        if key not in series:
            series[key] = _BarSeries(r[0], int(r[1]), [], [], [])
        s = series[key]
        s.closes.append(float(r[2]))
        s.start_ts.append(int(r[3]))
        s.end_ts.append(int(r[4]))
    return series


def _attach_entry_indices(
    trades: list[_BaselineTrade],
    series: dict[tuple[str, int], _BarSeries],
) -> None:
    for t in trades:
        s = series.get((t.symbol, t.tf_min))
        if s is None or not s.start_ts:
            t.entry_bar_idx = -1
            t.hold_bars = max(1, int(round(_hold_bars(t.opened_ts_ms, t.closed_ts_ms, t.tf_min))))
            continue
        # nearest bar start at/before open
        idx = 0
        for i, ts in enumerate(s.start_ts):
            if ts <= t.opened_ts_ms:
                idx = i
            else:
                break
        t.entry_bar_idx = idx
        t.hold_bars = max(1, int(round(_hold_bars(t.opened_ts_ms, t.closed_ts_ms, t.tf_min))))


def extract_trade_stats(
    conn: sqlite3.Connection,
    *,
    policy_hash: Optional[str] = None,
) -> TradeStats:
    trades = load_baseline_trades(conn, policy_hash=policy_hash)
    if not trades:
        return TradeStats(
            n_trades=0,
            long_frac=0.5,
            hold_bars_mean=1.0,
            hold_bars_by_tf={},
            baseline_mo=0.0,
            baseline_maxdd=0.0,
            by_tf={},
            mean_qty=1.0,
            symbols=(),
        )

    closed = [
        ClosedTrade(
            symbol=t.symbol,
            side=t.side,
            tf_min=t.tf_min,
            qty=t.qty,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            pnl=0.0,
            opened_ts_ms=t.opened_ts_ms,
            closed_ts_ms=t.closed_ts_ms,
            fees_usd=t.fees_usd,
            slippage_usd=t.slippage_usd,
            funding_usd=t.funding_usd,
        )
        for t in trades
    ]

    mo = mo_from_closed(closed)
    pnls = [_closed_trade_pnl(c) for c in closed]
    maxdd = max_drawdown_from_pnls(pnls)

    n_long = sum(1 for t in trades if t.side == Side.LONG)
    long_frac = n_long / len(trades)

    by_tf: dict[int, int] = {}
    hold_sum_by_tf: dict[int, float] = {}
    hold_n_by_tf: dict[int, int] = {}
    for t in trades:
        by_tf[t.tf_min] = by_tf.get(t.tf_min, 0) + 1
        hb = _hold_bars(t.opened_ts_ms, t.closed_ts_ms, t.tf_min)
        hold_sum_by_tf[t.tf_min] = hold_sum_by_tf.get(t.tf_min, 0.0) + hb
        hold_n_by_tf[t.tf_min] = hold_n_by_tf.get(t.tf_min, 0) + 1

    hold_bars_by_tf = {
        tf: hold_sum_by_tf[tf] / hold_n_by_tf[tf] for tf in hold_sum_by_tf
    }
    hold_bars_mean = sum(hold_sum_by_tf.values()) / max(1, sum(hold_n_by_tf.values()))
    mean_qty = sum(t.qty for t in trades) / len(trades)
    symbols = tuple(sorted({t.symbol for t in trades}))

    return TradeStats(
        n_trades=len(trades),
        long_frac=long_frac,
        hold_bars_mean=hold_bars_mean,
        hold_bars_by_tf=hold_bars_by_tf,
        baseline_mo=mo,
        baseline_maxdd=maxdd,
        by_tf=by_tf,
        mean_qty=mean_qty if mean_qty > 0 else 1.0,
        symbols=symbols,
    )


def _make_closed(
    *,
    symbol: str,
    tf_min: int,
    side: Side,
    qty: float,
    entry_price: float,
    exit_price: float,
    opened_ts_ms: int,
    closed_ts_ms: int,
    costs: CostModel,
) -> ClosedTrade:
    hold_hours = max(0.0, (closed_ts_ms - opened_ts_ms) / 3_600_000.0)
    fees, slip, funding = costs.round_trip_costs_usd(
        qty, entry_price, exit_price, hold_hours=hold_hours
    )
    if side == Side.LONG:
        gross = (exit_price - entry_price) * qty
    else:
        gross = (entry_price - exit_price) * qty
    pnl = gross - fees - slip - funding
    return ClosedTrade(
        symbol=symbol,
        side=side,
        tf_min=tf_min,
        qty=qty,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=pnl,
        opened_ts_ms=opened_ts_ms,
        closed_ts_ms=closed_ts_ms,
        fees_usd=fees,
        slippage_usd=slip,
        funding_usd=funding,
    )


def _matches_stats(
    trades: Sequence[ClosedTrade],
    stats: TradeStats,
) -> bool:
    if stats.n_trades <= 0:
        return len(trades) == 0
    n = len(trades)
    lo = stats.n_trades * (1.0 - _MATCH_TOL)
    hi = stats.n_trades * (1.0 + _MATCH_TOL)
    if n < lo or n > hi:
        return False
    if n == 0:
        return True
    long_frac = sum(1 for t in trades if t.side == Side.LONG) / n
    if abs(long_frac - stats.long_frac) > _MATCH_TOL:
        # absolute band of ±0.10 on fraction
        return False
    return True


def _target_counts(stats: TradeStats) -> dict[int, int]:
    if stats.n_trades <= 0:
        return {}
    # Preserve per-tf counts from baseline
    return dict(stats.by_tf)


def _sample_hold(rng: random.Random, mean_hold: float) -> int:
    # Geometric-ish around mean; at least 1 bar
    mean = max(1.0, mean_hold)
    # Use Poisson-like via discrete: round of exponential
    x = rng.expovariate(1.0 / mean)
    return max(1, int(round(x)))


def _simulate_entry_or_both(
    rng: random.Random,
    *,
    mode: MonkeyMode,
    stats: TradeStats,
    series: dict[tuple[str, int], _BarSeries],
    costs: CostModel,
) -> list[ClosedTrade]:
    """Random entries (+ random exits for both). Exit after hold bars for entry."""
    targets = _target_counts(stats)
    qty = stats.mean_qty
    out: list[ClosedTrade] = []

    for tf, n_target in targets.items():
        keys = [k for k in series if k[1] == tf and len(series[k]) >= 2]
        if not keys:
            continue
        mean_hold = stats.hold_bars_by_tf.get(tf, stats.hold_bars_mean)
        # Collect candidate (series_key, entry_idx) with room for ≥1 bar exit
        candidates: list[tuple[tuple[str, int], int]] = []
        for key in keys:
            s = series[key]
            for i in range(0, len(s) - 1):
                candidates.append((key, i))
        if not candidates:
            continue

        # Sample without replacement until target (with gentle oversample budget)
        need = n_target
        rng.shuffle(candidates)
        picked = candidates[: min(len(candidates), max(need * 3, need))]
        # Thin to approximately need entries
        if len(picked) > need:
            picked = rng.sample(picked, need)

        n_long = int(round(len(picked) * stats.long_frac))
        n_long = max(0, min(len(picked), n_long))
        sides = [Side.LONG] * n_long + [Side.SHORT] * (len(picked) - n_long)
        rng.shuffle(sides)

        for (key, entry_idx), side in zip(picked, sides):
            s = series[key]
            hold = (
                _sample_hold(rng, mean_hold)
                if mode == "both"
                else max(1, int(round(mean_hold)))
            )
            exit_idx = min(len(s) - 1, entry_idx + hold)
            if exit_idx <= entry_idx:
                exit_idx = min(len(s) - 1, entry_idx + 1)
            if exit_idx <= entry_idx:
                continue
            out.append(
                _make_closed(
                    symbol=s.symbol,
                    tf_min=s.tf_min,
                    side=side,
                    qty=qty,
                    entry_price=s.closes[entry_idx],
                    exit_price=s.closes[exit_idx],
                    opened_ts_ms=s.start_ts[entry_idx],
                    closed_ts_ms=s.end_ts[exit_idx],
                    costs=costs,
                )
            )
    return out


def _simulate_exit(
    rng: random.Random,
    *,
    baseline: Sequence[_BaselineTrade],
    stats: TradeStats,
    series: dict[tuple[str, int], _BarSeries],
    costs: CostModel,
) -> list[ClosedTrade]:
    """Baseline entries; random exit around mean hold."""
    out: list[ClosedTrade] = []
    for t in baseline:
        s = series.get((t.symbol, t.tf_min))
        mean_hold = stats.hold_bars_by_tf.get(t.tf_min, stats.hold_bars_mean)
        hold = _sample_hold(rng, mean_hold)
        if s is None or t.entry_bar_idx < 0 or not s.closes:
            # Fall back: keep entry price, synthesize exit via hold hours on flat price
            exit_price = t.entry_price
            closed_ts = t.opened_ts_ms + int(hold * t.tf_min * 60_000)
            out.append(
                _make_closed(
                    symbol=t.symbol,
                    tf_min=t.tf_min,
                    side=t.side,
                    qty=t.qty,
                    entry_price=t.entry_price,
                    exit_price=exit_price,
                    opened_ts_ms=t.opened_ts_ms,
                    closed_ts_ms=closed_ts,
                    costs=costs,
                )
            )
            continue
        entry_idx = min(t.entry_bar_idx, len(s) - 1)
        exit_idx = min(len(s) - 1, entry_idx + hold)
        if exit_idx <= entry_idx:
            exit_idx = min(len(s) - 1, entry_idx + 1)
        if exit_idx <= entry_idx:
            continue
        out.append(
            _make_closed(
                symbol=t.symbol,
                tf_min=t.tf_min,
                side=t.side,
                qty=t.qty,
                entry_price=s.closes[entry_idx],
                exit_price=s.closes[exit_idx],
                opened_ts_ms=s.start_ts[entry_idx],
                closed_ts_ms=s.end_ts[exit_idx],
                costs=costs,
            )
        )
    return out


def simulate_monkey_run(
    rng: random.Random,
    *,
    mode: MonkeyMode,
    stats: TradeStats,
    baseline: Sequence[_BaselineTrade],
    series: dict[tuple[str, int], _BarSeries],
    costs: CostModel,
) -> list[ClosedTrade]:
    for _ in range(_MAX_RESAMPLE):
        if mode == "exit":
            trades = _simulate_exit(
                rng, baseline=baseline, stats=stats, series=series, costs=costs
            )
        else:
            trades = _simulate_entry_or_both(
                rng, mode=mode, stats=stats, series=series, costs=costs
            )
        if _matches_stats(trades, stats):
            return trades
    # Last attempt — return even if slightly out of band (rare thin data)
    if mode == "exit":
        return _simulate_exit(
            rng, baseline=baseline, stats=stats, series=series, costs=costs
        )
    return _simulate_entry_or_both(
        rng, mode=mode, stats=stats, series=series, costs=costs
    )


def _run_metrics(trades: Sequence[ClosedTrade]) -> tuple[float, float]:
    if not trades:
        return 0.0, 0.0
    mo = mo_from_closed(trades)
    pnls = [_closed_trade_pnl(t) for t in trades]
    return mo, max_drawdown_from_pnls(pnls)


def run_mode(
    *,
    mode: MonkeyMode,
    stats: TradeStats,
    baseline: Sequence[_BaselineTrade],
    series: dict[tuple[str, int], _BarSeries],
    costs: CostModel,
    n_runs: int,
    seed: int,
    beat_threshold: float,
) -> MonkeyResult:
    mos: list[float] = []
    dds: list[float] = []
    for i in range(n_runs):
        rng = random.Random(seed + i * 1_000_003 + _MODE_SALT[mode] * 10_000)
        trades = simulate_monkey_run(
            rng,
            mode=mode,
            stats=stats,
            baseline=baseline,
            series=series,
            costs=costs,
        )
        mo, dd = _run_metrics(trades)
        mos.append(mo)
        dds.append(dd)

    n = len(mos) or 1
    mo_worse = sum(1 for m in mos if m < stats.baseline_mo) / n
    dd_worse = sum(1 for d in dds if d > stats.baseline_maxdd) / n
    sorted_mo = sorted(mos)
    dist = {
        "p5": _percentile(sorted_mo, 0.05),
        "p50": _percentile(sorted_mo, 0.50),
        "p95": _percentile(sorted_mo, 0.95),
    }
    passed = mo_worse >= beat_threshold and dd_worse >= beat_threshold
    return MonkeyResult(
        mode=mode,
        n_runs=n_runs,
        baseline_mo=stats.baseline_mo,
        baseline_maxdd=stats.baseline_maxdd,
        pct_runs_mo_worse=mo_worse,
        pct_runs_maxdd_worse=dd_worse,
        beat_threshold=beat_threshold,
        passed=passed,
        mo_distribution=dist,
        seed=seed,
        n_trades_baseline=stats.n_trades,
    )


def run_monkey_test(
    db_path: str | Path,
    *,
    policy_hash: Optional[str] = None,
    costs: Optional[CostModel] = None,
    modes: Sequence[MonkeyMode] = ALL_MODES,
    n_runs: int = 2000,
    seed: int = 42,
    beat_threshold: float = 0.90,
) -> list[MonkeyResult]:
    costs = costs or CostModel()
    conn = sqlite3.connect(str(db_path))
    try:
        stats = extract_trade_stats(conn, policy_hash=policy_hash)
        baseline = load_baseline_trades(conn, policy_hash=policy_hash)
        series = load_bar_series(conn)
        _attach_entry_indices(baseline, series)
    finally:
        conn.close()

    results: list[MonkeyResult] = []
    for mode in modes:
        results.append(
            run_mode(
                mode=mode,
                stats=stats,
                baseline=baseline,
                series=series,
                costs=costs,
                n_runs=n_runs,
                seed=seed,
                beat_threshold=beat_threshold,
            )
        )
    return results


def evaluate_monkey_gate(
    db_path: str | Path,
    *,
    trades_closed: int,
    min_closed_trades: int,
    require_monkey_pass: bool,
    monkey_beat_threshold: float,
    monkey_runs: int,
    monkey_seed: int,
    policy_hash: Optional[str] = None,
    costs: Optional[CostModel] = None,
    modes: Sequence[MonkeyMode] = ALL_MODES,
) -> MonkeyGateVerdict:
    if not require_monkey_pass:
        return MonkeyGateVerdict(status="skipped", reason="require_monkey_pass=False", seed=monkey_seed)
    if trades_closed < min_closed_trades:
        return MonkeyGateVerdict(
            status="insufficient_data",
            reason=(
                f"trades_closed={trades_closed} < min_closed_trades={min_closed_trades}; "
                "monkey gate not applied"
            ),
            seed=monkey_seed,
        )
    results = run_monkey_test(
        db_path,
        policy_hash=policy_hash,
        costs=costs,
        modes=modes,
        n_runs=monkey_runs,
        seed=monkey_seed,
        beat_threshold=monkey_beat_threshold,
    )
    all_pass = all(r.passed for r in results)
    return MonkeyGateVerdict(
        status="pass" if all_pass else "fail",
        results=tuple(results),
        reason="all modes pass" if all_pass else "one or more modes failed",
        seed=monkey_seed,
    )


def format_monkey_report(
    results: Sequence[MonkeyResult],
    *,
    policy_hash: str = "",
) -> str:
    if not results:
        return "MONKEY TEST — no results"
    r0 = results[0]
    lines = [
        f"MONKEY TEST — policy_hash={policy_hash or '?'}  "
        f"n_trades_baseline={r0.n_trades_baseline}  runs={r0.n_runs}  seed={r0.seed}",
        f"  baseline: Mo={r0.baseline_mo:.6f}  maxDD={r0.baseline_maxdd:.6f}",
    ]
    by_mode = {r.mode: r for r in results}
    for mode in ALL_MODES:
        r = by_mode.get(mode)
        if r is None:
            continue
        verdict = "PASS" if r.passed else "FAIL"
        lines.append(
            f"  {mode:5s}: beats {r.pct_runs_mo_worse * 100:.1f}% on Mo, "
            f"{r.pct_runs_maxdd_worse * 100:.1f}% on maxDD  -> {verdict}"
        )
    both = by_mode.get("both")
    if both is not None:
        d = both.mo_distribution
        lines.append(
            f"  monkey Mo distribution (both): "
            f"p5={d.get('p5', 0):.6f} p50={d.get('p50', 0):.6f} p95={d.get('p95', 0):.6f}"
        )
    overall = "PASS" if all(r.passed for r in results) else "FAIL"
    lines.append(f"  VERDICT: {overall} only if all three modes pass.")
    return "\n".join(lines)


def print_monkey_report(
    results: Sequence[MonkeyResult],
    *,
    policy_hash: str = "",
    json_path: str | Path | None = None,
) -> None:
    text = format_monkey_report(results, policy_hash=policy_hash)
    print(text)
    if json_path is not None:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "policy_hash": policy_hash,
            "seed": results[0].seed if results else None,
            "n_runs": results[0].n_runs if results else None,
            "n_trades_baseline": results[0].n_trades_baseline if results else None,
            "baseline_mo": results[0].baseline_mo if results else None,
            "baseline_maxdd": results[0].baseline_maxdd if results else None,
            "verdict": "PASS" if results and all(r.passed for r in results) else "FAIL",
            "modes": [asdict(r) for r in results],
            "report_text": text,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
