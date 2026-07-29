"""
Momentum vs mean-reversion diagnostics (Chan-inspired).

Offline analyser over recorded ``bars``. Does NOT change trading behaviour
or ``policy_hash``. Measures the *premise* of time-series momentum on our
symbols/TFs (Hurst, Variance Ratio, lagged return correlation).

Hurst is computed on **log-returns** via classical R/S regression.
Interpretation (rough): H>0.5 trend / momentum; H<0.5 mean-reversion; H≈0.5 RW.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Sequence


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _variance(xs: Sequence[float], *, ddof: int = 1) -> float:
    n = len(xs)
    if n <= ddof:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - ddof)


def log_returns(closes: Sequence[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a <= 0 or b <= 0:
            continue
        out.append(math.log(b / a))
    return out


def hurst_exponent(closes: Sequence[float]) -> float:
    """
    Hurst exponent via R/S on log-returns.

    Returns NaN if series is too short. H≈0.5 random walk; >0.5 trending;
    <0.5 mean-reverting.
    """
    rets = log_returns(closes)
    n = len(rets)
    if n < 64:
        return float("nan")

    # lag sizes: powers of two up to n/2
    max_k = int(math.log2(n // 2))
    if max_k < 3:
        return float("nan")
    lags = [2**k for k in range(3, max_k + 1)]
    log_n: list[float] = []
    log_rs: list[float] = []
    for lag in lags:
        rs_vals: list[float] = []
        for start in range(0, n - lag + 1, lag):
            chunk = rets[start : start + lag]
            m = _mean(chunk)
            deviations = [x - m for x in chunk]
            cum = []
            s = 0.0
            for d in deviations:
                s += d
                cum.append(s)
            r = max(cum) - min(cum)
            std = math.sqrt(_variance(chunk, ddof=0))
            if std <= 0 or r <= 0:
                continue
            rs_vals.append(r / std)
        if not rs_vals:
            continue
        log_n.append(math.log(lag))
        log_rs.append(math.log(_mean(rs_vals)))

    if len(log_n) < 2:
        return float("nan")
    # OLS slope
    x_bar = _mean(log_n)
    y_bar = _mean(log_rs)
    num = sum((x - x_bar) * (y - y_bar) for x, y in zip(log_n, log_rs))
    den = sum((x - x_bar) ** 2 for x in log_n)
    if den <= 0:
        return float("nan")
    return num / den


def variance_ratio(closes: Sequence[float], q: int) -> tuple[float, float]:
    """
    Lo–MacKinlay variance ratio on log-returns (homoskedastic z-stat).

    Returns (VR, z). VR>1 momentum-ish; VR<1 mean-reversion-ish.
    """
    if q < 2:
        raise ValueError("q must be >= 2")
    rets = log_returns(closes)
    n = len(rets)
    if n < q * 4:
        return float("nan"), float("nan")

    mu = _mean(rets)
    # one-period variance
    var1 = sum((r - mu) ** 2 for r in rets) / (n - 1)
    if var1 <= 0:
        return float("nan"), float("nan")

    # q-period overlapping returns
    rq = [sum(rets[i : i + q]) for i in range(0, n - q + 1)]
    m = n - q + 1
    var_q = sum((x - q * mu) ** 2 for x in rq) / (m - 1)
    vr = var_q / (q * var1)

    # Homoskedastic asymptotic variance of VR (Lo-MacKinlay 1988)
    # Var(VR) ≈ (2(2q-1)(q-1))/(3q(n))
    phi = (2 * (2 * q - 1) * (q - 1)) / (3 * q * n)
    if phi <= 0:
        return vr, float("nan")
    z = (vr - 1.0) / math.sqrt(phi)
    return vr, z


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return float("nan"), float("nan")
    mx, my = _mean(xs), _mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x <= 0 or den_y <= 0:
        return float("nan"), float("nan")
    r = num / (den_x * den_y)
    r = max(-0.999999, min(0.999999, r))
    # two-sided p via t ~ Student(n-2), normal approx for large n
    t = r * math.sqrt((n - 2) / (1 - r * r))
    # approximate p with erfc of |t| / sqrt(2) (normal)
    p = math.erfc(abs(t) / math.sqrt(2.0))
    return r, p


def lagged_return_correlation(
    closes: Sequence[float],
    start_ts: Sequence[int] | None = None,
    *,
    lookback_bars: int,
    hold_bars: int,
    step: Optional[int] = None,
) -> tuple[float, float]:
    """
    Corr(past return, future return) on Chan non-overlapping schedule.

    Past: return over ``lookback_bars`` closes ending at t.
    Future: return over next ``hold_bars`` closes.
    Default step (Chan AT ch.6): min(lookback, hold) —
    if lookback>hold shift by hold; if hold>lookback shift by lookback.

    ``start_ts`` accepted for API symmetry / future time-aware cuts; unused
    when sampling by bar index.
    """
    _ = start_ts
    if lookback_bars < 1 or hold_bars < 1:
        raise ValueError("lookback_bars and hold_bars must be >= 1")
    if step is None:
        step = hold_bars if lookback_bars > hold_bars else lookback_bars
    if step < 1:
        raise ValueError("step must be >= 1")

    n = len(closes)
    past: list[float] = []
    fut: list[float] = []
    t = lookback_bars
    while t + hold_bars < n:
        p0, p1 = closes[t - lookback_bars], closes[t]
        f0, f1 = closes[t], closes[t + hold_bars]
        if p0 > 0 and p1 > 0 and f0 > 0 and f1 > 0:
            past.append(math.log(p1 / p0))
            fut.append(math.log(f1 / f0))
        t += step
    return _pearson(past, fut)


@dataclass(frozen=True)
class CellDiag:
    symbol: str
    tf_min: int
    n_bars: int
    hurst: float
    vr: dict[str, dict[str, float]]  # q -> {vr, z}
    lag_corr: float
    lag_p: float
    premise: str  # momentum | mean_reversion | random_walk | insufficient_data


def _classify(h: float, vr_vals: list[float]) -> str:
    if math.isnan(h):
        return "insufficient_data"
    vr_mean = _mean([v for v in vr_vals if not math.isnan(v)]) if vr_vals else float("nan")
    if h > 0.55 or (not math.isnan(vr_mean) and vr_mean > 1.05):
        return "momentum"
    if h < 0.45 or (not math.isnan(vr_mean) and vr_mean < 0.95):
        return "mean_reversion"
    return "random_walk"


def diagnose(
    db_path: str | Path,
    *,
    policy_hash: Optional[str] = None,
    vr_horizons: Sequence[int] = (2, 4, 8, 16),
    lookback_bars: int = 8,
    hold_bars: int = 4,
) -> dict[str, Any]:
    """
    Per (symbol, tf) diagnostics. ``policy_hash`` reserved for future trade
    filtering; bars table has no policy column — currently unused.
    """
    _ = policy_hash
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT symbol, tf_min, close, start_ts_ms
            FROM bars
            ORDER BY symbol, tf_min, start_ts_ms ASC
            """
        ).fetchall()
    finally:
        conn.close()

    series: dict[tuple[str, int], tuple[list[float], list[int]]] = {}
    for symbol, tf_min, close, start_ts in rows:
        key = (symbol, int(tf_min))
        if key not in series:
            series[key] = ([], [])
        series[key][0].append(float(close))
        series[key][1].append(int(start_ts))

    cells: list[CellDiag] = []
    for (symbol, tf_min), (closes, starts) in sorted(series.items()):
        h = hurst_exponent(closes)
        vr_map: dict[str, dict[str, float]] = {}
        vr_list: list[float] = []
        for q in vr_horizons:
            vr, z = variance_ratio(closes, int(q))
            vr_map[str(q)] = {"vr": vr, "z": z}
            vr_list.append(vr)
        corr, p = lagged_return_correlation(
            closes, starts, lookback_bars=lookback_bars, hold_bars=hold_bars
        )
        cells.append(
            CellDiag(
                symbol=symbol,
                tf_min=tf_min,
                n_bars=len(closes),
                hurst=h,
                vr=vr_map,
                lag_corr=corr,
                lag_p=p,
                premise=_classify(h, vr_list),
            )
        )

    return {
        "n_cells": len(cells),
        "params": {
            "hurst_on": "log_returns_RS",
            "vr_horizons": list(vr_horizons),
            "lag_lookback_bars": lookback_bars,
            "lag_hold_bars": hold_bars,
            "lag_step_rule": "min(lookback, hold) per Chan AT ch.6",
        },
        "cells": [asdict(c) for c in cells],
    }


def format_diag_table(payload: dict[str, Any]) -> str:
    lines = [
        "MOMENTUM DIAGNOSTICS (informational; not a pass criterion)",
        f"  cells={payload.get('n_cells', 0)}  hurst_on=log_returns R/S",
        "  symbol     tf   n_bars   Hurst     VR(q=2)   VR(q=8)   lag_r     lag_p   premise",
    ]
    for c in payload.get("cells") or []:
        vr2 = (c.get("vr") or {}).get("2", {}).get("vr", float("nan"))
        vr8 = (c.get("vr") or {}).get("8", {}).get("vr", float("nan"))

        def _f(x: float) -> str:
            return f"{x:8.3f}" if isinstance(x, (int, float)) and not math.isnan(x) else "     nan"

        lines.append(
            f"  {c['symbol']:<10}{c['tf_min']:>4}  {c['n_bars']:>6}  "
            f"{_f(c['hurst'])}  {_f(vr2)}  {_f(vr8)}  "
            f"{_f(c['lag_corr'])}  {_f(c['lag_p'])}  {c['premise']}"
        )
    return "\n".join(lines)


def print_momentum_diag(
    db_path: str | Path,
    *,
    json_path: str | Path | None = None,
    policy_hash: Optional[str] = None,
) -> dict[str, Any]:
    payload = diagnose(db_path, policy_hash=policy_hash)
    text = format_diag_table(payload)
    print(text)
    if json_path is not None:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        out = {**payload, "report_text": text, "policy_hash": policy_hash}
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {path}")
    return payload
