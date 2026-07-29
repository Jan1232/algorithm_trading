from __future__ import annotations

from typing import Any, Optional

from bot.models import Bar, Signal, SignalKind


def _safe_k_long(bar: Bar) -> float:
    rng = bar.range
    if rng <= 0:
        return 0.0
    return max(0.0, min(1.0, (bar.close - bar.open) / rng))


def _safe_k_short(bar: Bar) -> float:
    rng = bar.range
    if rng <= 0:
        return 0.0
    return max(0.0, min(1.0, (bar.open - bar.close) / rng))


def compute_delta(bar: Bar, *, for_long: bool) -> tuple[float, float]:
    """Return (k, delta) from the bar itself — no historical optimization."""
    k = _safe_k_long(bar) if for_long else _safe_k_short(bar)
    delta = bar.range * (1.0 - k)
    return k, delta


def _build_checks(bar: Bar, prev: Bar) -> dict[str, Any]:
    k_long, d_long = compute_delta(bar, for_long=True)
    k_short, d_short = compute_delta(bar, for_long=False)
    hh = bar.high > prev.high
    hl = bar.low > prev.low
    ll = bar.low < prev.low
    lh = bar.high < prev.high
    long_mid = bar.mid > bar.low + d_long
    short_mid = bar.mid < bar.high - d_short
    return {
        "rule1_long": {
            "H_i > H_i-1": hh,
            "L_i > L_i-1": hl,
            "mid > L_i + Δ": long_mid,
            "k": k_long,
            "delta": d_long,
            "mid": bar.mid,
            "L+Δ": bar.low + d_long,
            "passed": hh and hl and long_mid,
        },
        "rule2_short": {
            "L_i < L_i-1": ll,
            "H_i < H_i-1": lh,
            "mid < H_i − Δ": short_mid,
            "k": k_short,
            "delta": d_short,
            "mid": bar.mid,
            "H−Δ": bar.high - d_short,
            "passed": ll and lh and short_mid,
        },
        "bar": {
            "O": bar.open,
            "H": bar.high,
            "L": bar.low,
            "C": bar.close,
            "range": bar.range,
        },
        "prev": {
            "O": prev.open,
            "H": prev.high,
            "L": prev.low,
            "C": prev.close,
        },
    }


def evaluate_signal(bar: Bar, prev: Bar) -> Signal:
    """
    Parameter-free rules 1–3 from TZ §5. Decision on closed bar only.

    Algebraic identity (same bar for k and Delta, look-ahead-free on close of i):
      mid > L + Delta  with  Delta = (H-L)*(1-k_long)
      <=>  k_long > 0.5
      <=>  (C-O) > (H-L)/2   (close in upper half of the bar)

    Same for short with k_short. So Delta is not an independent parameter —
    the third inequality collapses to a 0.5 threshold on close placement,
    plus HH/HL (or LL/LH) structure filters.
    """
    if bar.symbol != prev.symbol or bar.tf_min != prev.tf_min:
        raise ValueError("bar and prev must share symbol and timeframe")

    checks = _build_checks(bar, prev)
    k_long = checks["rule1_long"]["k"]
    d_long = checks["rule1_long"]["delta"]
    k_short = checks["rule2_short"]["k"]
    d_short = checks["rule2_short"]["delta"]

    # Explicit equivalent form (must stay in sync with mid/Delta checks)
    checks["rule1_long"]["k_gt_half"] = k_long > 0.5
    checks["rule1_long"]["equiv_passed"] = (
        checks["rule1_long"]["H_i > H_i-1"]
        and checks["rule1_long"]["L_i > L_i-1"]
        and k_long > 0.5
    )
    checks["rule2_short"]["k_gt_half"] = k_short > 0.5
    checks["rule2_short"]["equiv_passed"] = (
        checks["rule2_short"]["L_i < L_i-1"]
        and checks["rule2_short"]["H_i < H_i-1"]
        and k_short > 0.5
    )

    if checks["rule1_long"]["passed"]:
        reason = (
            f"rule1 LONG tf={bar.tf_min}: HH&HL and mid>{bar.low + d_long:.6f} "
            f"(k={k_long:.4f}>0.5, delta={d_long:.6f})"
        )
        return Signal(
            kind=SignalKind.LONG,
            symbol=bar.symbol,
            tf_min=bar.tf_min,
            bar=bar,
            prev_bar=prev,
            k=k_long,
            delta=d_long,
            reason=reason,
            checks=checks,
        )

    if checks["rule2_short"]["passed"]:
        reason = (
            f"rule2 SHORT tf={bar.tf_min}: LL&LH and mid<{bar.high - d_short:.6f} "
            f"(k={k_short:.4f}>0.5, delta={d_short:.6f})"
        )
        return Signal(
            kind=SignalKind.SHORT,
            symbol=bar.symbol,
            tf_min=bar.tf_min,
            bar=bar,
            prev_bar=prev,
            k=k_short,
            delta=d_short,
            reason=reason,
            checks=checks,
        )

    reason = (
        f"rule3 FLAT tf={bar.tf_min}: neither rule1 nor rule2 "
        f"(long_ok={checks['rule1_long']['passed']}, short_ok={checks['rule2_short']['passed']})"
    )
    return Signal(
        kind=SignalKind.FLAT,
        symbol=bar.symbol,
        tf_min=bar.tf_min,
        bar=bar,
        prev_bar=prev,
        k=0.0,
        delta=0.0,
        reason=reason,
        checks=checks,
    )


class SignalCore:
    """Stateful evaluator: needs previous closed bar per (symbol, tf)."""

    def __init__(self) -> None:
        self._prev: dict[tuple[str, int], Bar] = {}

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        key = (bar.symbol, bar.tf_min)
        prev = self._prev.get(key)
        self._prev[key] = bar
        if prev is None:
            return None
        return evaluate_signal(bar, prev)

    def reset(self) -> None:
        self._prev.clear()
