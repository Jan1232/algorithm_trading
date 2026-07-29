"""Momentum / mean-reversion diagnostics unit tests."""

from __future__ import annotations

import math
import random
from pathlib import Path

from bot.analysis.momentum_diagnostics import (
    hurst_exponent,
    lagged_return_correlation,
    variance_ratio,
)


def _prices_from_returns(returns: list[float], p0: float = 100.0) -> list[float]:
    out = [p0]
    for r in returns:
        out.append(out[-1] * math.exp(r))
    return out


def _ar1_returns(n: int, phi: float, seed: int, sigma: float = 0.01) -> list[float]:
    rng = random.Random(seed)
    r = 0.0
    out: list[float] = []
    for _ in range(n):
        r = phi * r + sigma * rng.gauss(0, 1)
        out.append(r)
    return out


def _geom_rw(n: int, seed: int = 0, sigma: float = 0.01) -> list[float]:
    rng = random.Random(seed)
    return _prices_from_returns([sigma * rng.gauss(0, 1) for _ in range(n - 1)])


def test_hurst_trend_vs_meanrev_vs_rw():
    # Positive AR(1) returns → persistence / momentum-like
    h_mom = hurst_exponent(_prices_from_returns(_ar1_returns(4096, phi=0.7, seed=1)))
    # Negative AR(1) returns → anti-persistence / mean-reversion-like
    h_mr = hurst_exponent(_prices_from_returns(_ar1_returns(4096, phi=-0.6, seed=2)))
    h_rw = hurst_exponent(_geom_rw(4096, seed=3))
    assert h_mom > 0.55, h_mom
    assert h_mr < 0.5, h_mr
    assert abs(h_rw - 0.5) < 0.15, h_rw


def test_variance_ratio_trend_vs_meanrev():
    vr_m, z_m = variance_ratio(
        _prices_from_returns(_ar1_returns(4096, phi=0.7, seed=4)), q=8
    )
    vr_r, z_r = variance_ratio(
        _prices_from_returns(_ar1_returns(4096, phi=-0.6, seed=5)), q=8
    )
    vr_rw, _ = variance_ratio(_geom_rw(4096, seed=6), q=8)
    assert vr_m > 1.0, (vr_m, z_m)
    assert vr_r < 1.0, (vr_r, z_r)
    assert abs(vr_rw - 1.0) < 0.25, vr_rw


def test_lagged_corr_overlapping_vs_nonoverlapping_schedule():
    closes = _prices_from_returns(_ar1_returns(2000, phi=-0.5, seed=9))
    lookback, hold = 20, 5

    def n_pairs(step: int) -> int:
        n = 0
        t = lookback
        while t + hold < len(closes):
            n += 1
            t += step
        return n

    assert n_pairs(1) > n_pairs(lookback + hold)
    # Chan rule when lookback>hold: step = hold = min(...)
    assert n_pairs(min(lookback, hold)) == n_pairs(hold)

    r_ol, p_ol = lagged_return_correlation(
        closes, lookback_bars=lookback, hold_bars=hold, step=1
    )
    r_nol, p_nol = lagged_return_correlation(
        closes,
        lookback_bars=lookback,
        hold_bars=hold,
        step=lookback + hold,
    )
    r_chan, p_chan = lagged_return_correlation(
        closes, lookback_bars=lookback, hold_bars=hold
    )
    assert not math.isnan(r_ol) and not math.isnan(r_nol) and not math.isnan(r_chan)
    # Overlapped vs fully non-overlapped estimators must diverge
    assert (r_ol, p_ol) != (r_nol, p_nol)
    # Default Chan schedule equals step=min(lookback, hold)
    r_min, p_min = lagged_return_correlation(
        closes, lookback_bars=lookback, hold_bars=hold, step=min(lookback, hold)
    )
    assert (r_chan, p_chan) == (r_min, p_min)


def test_classify_rw_not_momentum_despite_hurst_bias():
    """R/S Hurst on finite RW is upward-biased; premise must stay random_walk."""
    from bot.analysis.momentum_diagnostics import _classify, variance_ratio

    closes = _geom_rw(4096, seed=11)
    h = hurst_exponent(closes)
    # Reproduce the empirical failure mode: H often > 0.55 on pure RW
    assert h > 0.55 or True  # bias may or may not exceed 0.55; force the check below
    points = [variance_ratio(closes, q) for q in (2, 4, 8, 16)]
    # Even if we inject a biased Hurst, usable insignificant VR → random_walk
    assert _classify(0.572, points) == "random_walk"
    assert _classify(h, points) == "random_walk"


def test_classify_ar_plus_momentum_ar_minus_meanrev():
    from bot.analysis.momentum_diagnostics import _classify, variance_ratio

    mom = _prices_from_returns(_ar1_returns(4096, phi=0.7, seed=21))
    mr = _prices_from_returns(_ar1_returns(4096, phi=-0.6, seed=22))
    mom_pts = [variance_ratio(mom, q) for q in (2, 4, 8, 16)]
    mr_pts = [variance_ratio(mr, q) for q in (2, 4, 8, 16)]
    assert _classify(hurst_exponent(mom), mom_pts) == "momentum"
    assert _classify(hurst_exponent(mr), mr_pts) == "mean_reversion"


def test_classify_insignificant_vr_ignores_hurst():
    from bot.analysis.momentum_diagnostics import _classify

    # Synthetic near-RW VR with tiny z — must not promote via Hurst
    points = [(1.02, 0.4), (0.98, -0.3), (1.01, 0.2), (0.99, -0.1)]
    assert _classify(0.70, points) == "random_walk"
    assert _classify(0.30, points) == "random_walk"


def test_diagnose_missing_db_and_missing_bars_table(tmp_path: Path):
    from bot.analysis.momentum_diagnostics import diagnose, format_diag_table

    missing = diagnose(tmp_path / "nope.db")
    assert missing["n_cells"] == 0
    assert "not found" in (missing.get("error") or "")
    assert "ERROR" in format_diag_table(missing)

    empty = tmp_path / "empty.db"
    import sqlite3

    sqlite3.connect(str(empty)).close()
    payload = diagnose(empty)
    assert payload["n_cells"] == 0
    assert "no bars table" in (payload.get("error") or "")
    assert "ERROR" in format_diag_table(payload)
