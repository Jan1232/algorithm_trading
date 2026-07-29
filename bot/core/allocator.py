from __future__ import annotations

from dataclasses import dataclass

from bot.core.risk import TfDrawdownTracker, filter_signals_by_tf_risk
from bot.models import Signal, SignalKind, Side


@dataclass(frozen=True)
class Allocation:
    signal: Signal
    notional_usd: float
    qty: float
    stop_price: float
    risk_usd: float


def allocate_by_stop_risk(
    signals: list[Signal],
    *,
    kind: SignalKind,
    deposit: float,
    price: float,
    tracker: TfDrawdownTracker,
    risk_pct: float,
    max_drawdown_pct: float,
) -> list[Allocation]:
    """
    Equal dollar risk-to-stop among eligible TF (not equal notional).

    Each TF gets risk_budget = (deposit * max_drawdown_pct) / N_R.
    qty = risk_budget / |price - stop|, stop from prev bar extreme.
    """
    if kind not in (SignalKind.LONG, SignalKind.SHORT):
        return []
    if price <= 0 or deposit <= 0:
        return []

    candidates = [s for s in signals if s.kind == kind]
    eligible = filter_signals_by_tf_risk(candidates, tracker, deposit, risk_pct)
    if not eligible:
        return []

    risk_budget = (deposit * max_drawdown_pct) / len(eligible)
    max_notional = deposit / len(eligible)
    out: list[Allocation] = []
    for sig in eligible:
        prev = sig.prev_bar
        if prev is None:
            continue
        if kind == SignalKind.LONG:
            stop = prev.high
            protect = prev.low
            side = Side.LONG
        else:
            stop = prev.low
            protect = prev.high
            side = Side.SHORT
        stop_dist = abs(price - protect)
        if stop_dist <= 0:
            continue
        qty = risk_budget / stop_dist
        notional = qty * price
        # Cap: never exceed equal-share notional (avoids insane size on tiny stops)
        if notional > max_notional:
            qty = max_notional / price
            notional = max_notional
        out.append(
            Allocation(
                signal=sig,
                notional_usd=notional,
                qty=qty,
                stop_price=stop,
                risk_usd=min(risk_budget, abs(price - protect) * qty),
            )
        )
        _ = side
    return out


# Back-compat name used by older tests
def allocate_deposit(
    signals: list[Signal],
    *,
    kind: SignalKind,
    deposit: float,
    price: float,
    tracker: TfDrawdownTracker,
    risk_pct: float,
    max_drawdown_pct: float = 0.10,
) -> list[Allocation]:
    return allocate_by_stop_risk(
        signals,
        kind=kind,
        deposit=deposit,
        price=price,
        tracker=tracker,
        risk_pct=risk_pct,
        max_drawdown_pct=max_drawdown_pct,
    )
