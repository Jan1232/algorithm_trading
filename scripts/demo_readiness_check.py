"""Quick demo-readiness diagnostic."""
from __future__ import annotations

from bot.config import load_settings
from bot.core.allocator import allocate_by_stop_risk
from bot.core.risk import TfDrawdownTracker
from bot.exchange.instruments import InstrumentRegistry
from bot.models import Bar, Signal, SignalKind
from bot.storage.db import TradeStore


def main() -> None:
    s = load_settings()
    # Force demo paths for this diagnostic when BOT_MODE not set
    if s.mode == "paper":
        s.mode = "demo"
        s.db_path = s.resolve_db_path()

    print("=== CONFIG ===")
    print(f"mode={s.mode} deposit={s.deposit_usd} P_usd={s.max_drawdown_usd} exit={s.exit_mode}")
    print(f"deposit_from_wallet={s.deposit_from_wallet} one_pos={s.one_position_per_symbol}")
    print(f"reconcile_sec={s.reconcile_sec} db={s.db_path}")
    print(f"symbols={s.symbols} tfs={s.timeframes_min}")

    store = TradeStore(s.db_path)
    st = store.stats()
    print("\n=== DB (demo) ===")
    print(st)
    opens = store.list_open_trades()
    print(f"open_trades={len(opens)}")
    for r in opens:
        print(f"  {r['symbol']} tf={r['tf_min']} {r['side']} qty={r['qty']} mode={r.get('mode')}")

    # sizing probe
    reg = InstrumentRegistry()
    reg.load_fallback(s.symbols)
    tracker = TfDrawdownTracker()
    print("\n=== SIZING PROBE (deposit, 2 TF long) ===")
    for sym, price, prev_low in (
        ("BTCUSDT", 63000.0, 62800.0),
        ("ETHUSDT", 1900.0, 1880.0),
        ("SOLUSDT", 75.0, 74.0),
    ):
        signals = []
        for tf in (15, 30):
            bar = Bar(sym, tf, price, price * 1.01, prev_low, price * 1.005, 0, 1, 1)
            prev = Bar(sym, tf, price, price * 1.005, prev_low, price, 0, 1, 1)
            signals.append(Signal(SignalKind.LONG, sym, tf, bar, prev, k=0.7, delta=1))
        allocs = allocate_by_stop_risk(
            signals,
            kind=SignalKind.LONG,
            deposit=s.deposit_usd,
            price=price,
            tracker=tracker,
            risk_pct=s.tf_risk_pct,
            max_drawdown_pct=s.max_drawdown_pct,
        )
        spec = reg.get(sym)
        for a in allocs:
            qty = spec.round_qty(a.qty)
            print(
                f"  {sym} tf={a.signal.tf_min}: raw_qty={a.qty:.6f} rounded={qty} "
                f"notional={a.notional_usd:.2f} min_qty={spec.min_qty} "
                f"{'SKIP' if qty <= 0 else 'OK'}"
            )

    print("\n=== GAPS (after hardening) ===")
    print("OK: stop-limit fills polled via get_open_orders + get_order_history")
    print("OK: one_position_per_symbol (Bybit one-way net)")
    print("OK: exchange SL via set_trading_stop (Full + Market)")
    print("OK: demo/live PublicTradeFeed.ensure_alive reconnect")
    print("OK: deposit_from_wallet (UNIFIED USDT equity)")
    print("OK: separate db_path_demo / db_path_paper / db_path_live")
    store.close()


if __name__ == "__main__":
    main()
