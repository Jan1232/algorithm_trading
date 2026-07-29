from __future__ import annotations

from dataclasses import dataclass

from bot.core.risk import TfDrawdownTracker, filter_signals_by_tf_risk
from bot.models import Signal, SignalKind, Side


@dataclass(frozen=True)
class Allocation:
    signal: Signal
    notional_usd: float
    qty: float
    entry_trigger: float  # breakout extreme of prev bar (entry trigger)
    protect_stop: float  # protective stop (prev.low LONG / prev.high SHORT)
    risk_usd: float


def allocate_by_stop_risk(
    signals: list[Signal],
    *,
    kind: SignalKind,
    deposit: float,
    price: float,
    tracker: TfDrawdownTracker,
    risk_pct: float,
    max_drawdown_pct: float = 0.10,
    per_trade_risk_pct: float = 0.02,
    max_leverage_frac: float = 1.0,
) -> list[Allocation]:
    """
    Risk-to-stop sizing per eligible TF.

    risk_budget = deposit * per_trade_risk_pct (NOT P / N_R).
    qty = risk_budget / |price - protect_stop|.
    max_notional = deposit * max_leverage_frac.
    max_drawdown_pct kept for call-site back-compat; unused for sizing.
    """
    _ = max_drawdown_pct
    if kind not in (SignalKind.LONG, SignalKind.SHORT):
        return []
    if price <= 0 or deposit <= 0:
        return []
    if per_trade_risk_pct <= 0:
        return []

    candidates = [s for s in signals if s.kind == kind]
    eligible = filter_signals_by_tf_risk(candidates, tracker, deposit, risk_pct)
    if not eligible:
        return []

    risk_budget = deposit * per_trade_risk_pct
    max_notional = deposit * max_leverage_frac
    out: list[Allocation] = []
    for sig in eligible:
        prev = sig.prev_bar
        if prev is None:
            continue
        if kind == SignalKind.LONG:
            trigger = prev.high
            protect = prev.low
            side = Side.LONG
        else:
            trigger = prev.low
            protect = prev.high
            side = Side.SHORT
        stop_dist = abs(price - protect)
        if stop_dist <= 0:
            continue
        qty = risk_budget / stop_dist
        notional = qty * price
        if notional > max_notional:
            qty = max_notional / price
            notional = max_notional
        out.append(
            Allocation(
                signal=sig,
                notional_usd=notional,
                qty=qty,
                entry_trigger=trigger,
                protect_stop=protect,
                risk_usd=min(risk_budget, abs(price - protect) * qty),
            )
        )
        _ = side
    return out


def allocate_deposit(
    signals: list[Signal],
    *,
    kind: SignalKind,
    deposit: float,
    price: float,
    tracker: TfDrawdownTracker,
    risk_pct: float,
    max_drawdown_pct: float = 0.10,
    per_trade_risk_pct: float = 0.02,
    max_leverage_frac: float = 1.0,
) -> list[Allocation]:
    return allocate_by_stop_risk(
        signals,
        kind=kind,
        deposit=deposit,
        price=price,
        tracker=tracker,
        risk_pct=risk_pct,
        max_drawdown_pct=max_drawdown_pct,
        per_trade_risk_pct=per_trade_risk_pct,
        max_leverage_frac=max_leverage_frac,
    )
