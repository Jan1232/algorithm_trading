"""CLI report over SQLite + kill-switch alert sink."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from bot.config import load_settings
from bot.core.validator import StabilityValidator
from bot.models import ClosedTrade, Side
from bot.storage.db import TradeStore

logger = logging.getLogger(__name__)


def write_alert(root: Path, kind: str, message: str, extra: dict | None = None) -> None:
    path = root / "logs" / "alerts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "message": message,
        "extra": extra or {},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.warning("ALERT [%s] %s", kind, message)


def print_report(db_path: str) -> None:
    store = TradeStore(db_path)
    st = store.stats()
    print("=== SQLite stats ===")
    print(json.dumps(st, ensure_ascii=False, indent=2))

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT symbol, tf_min, side, qty, entry_price, exit_price, pnl, s,
               entry_reason, exit_reason, opened_ts_ms, closed_ts_ms
        FROM trades WHERE status='closed' ORDER BY id DESC LIMIT 20
        """
    ).fetchall()
    print("\n=== Last closed trades ===")
    for r in rows:
        print(
            f"{r['symbol']} tf={r['tf_min']} {r['side']} pnl={r['pnl']:.4f} s={r['s']} "
            f"| in: {r['entry_reason'][:60] if r['entry_reason'] else ''} "
            f"| out: {r['exit_reason']}"
        )

    # Mo from closed trades (skip orphans / rows without exit_price)
    v = StabilityValidator()
    for r in conn.execute(
        """
        SELECT symbol, tf_min, side, qty, entry_price, exit_price, pnl,
               opened_ts_ms, closed_ts_ms,
               COALESCE(fees_usd,0), COALESCE(slippage_usd,0), COALESCE(funding_usd,0),
               COALESCE(mode,'unknown'), COALESCE(exit_reason,'')
        FROM trades WHERE status='closed'
        """
    ):
        if r[5] is None or r[13] == "sync_orphan":
            continue
        side = Side.LONG if r[2] == "long" else Side.SHORT
        v.add(
            ClosedTrade(
                symbol=r[0],
                side=side,
                tf_min=r[1],
                qty=r[3],
                entry_price=r[4],
                exit_price=r[5],
                pnl=r[6],
                opened_ts_ms=r[7] or 0,
                closed_ts_ms=r[8] or 0,
                fees_usd=float(r[9] or 0),
                slippage_usd=float(r[10] or 0),
                funding_usd=float(r[11] or 0),
            )
        )
    print("\n=== Stability ===")
    print(v.report().summary())

    # Per-TF pnl
    print("\n=== PnL by TF ===")
    for r in conn.execute(
        "SELECT tf_min, COUNT(*), SUM(pnl) FROM trades WHERE status='closed' GROUP BY tf_min ORDER BY tf_min"
    ):
        print(f"tf={r[0]} n={r[1]} pnl={r[2]:.4f}")

    print("\n=== By mode ===")
    for r in conn.execute(
        "SELECT COALESCE(mode,'unknown'), status, COUNT(*), COALESCE(SUM(pnl),0) "
        "FROM trades GROUP BY mode, status ORDER BY mode, status"
    ):
        print(f"mode={r[0]} status={r[1]} n={r[2]} pnl={r[3]:.4f}")

    echelon2 = conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind='echelon2_block'"
    ).fetchone()[0]
    print(f"\nechelon2_blocks: {echelon2}")

    open_n = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    print(f"open trades: {open_n}")
    conn.close()
    store.close()


def print_cabinet(db_path: str) -> None:
    """
    Читать текущие открытые позиции в "человеческом" виде.

    Мы вычисляем trailing-stop по последней закрытой свече нужного tf:
    - long: stop = last_bar.low
    - short: stop = last_bar.high

    А для `hybrid` показываем, что последняя свеча не стала FLAT (иначе позицию уже бы закрыли).
    """
    import sys

    # Ensure UTF-8 output on Windows terminals (avoid "����" mojibake)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    settings = load_settings()
    exit_mode = settings.exit_mode

    import sqlite3
    from math import isnan

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    open_rows = conn.execute(
        """
        SELECT id, symbol, tf_min, side, qty, entry_price, opened_ts_ms,
               entry_reason, exit_reason, market_json, mode, policy_hash
        FROM trades
        WHERE status='open'
        ORDER BY opened_ts_ms DESC
        """
    ).fetchall()

    if not open_rows:
        print("=== Cabinet ===")
        print("Открытых позиций нет.")
        conn.close()
        return

    print("=== Cabinet: текущие открытые позиции ===")
    print(f"exit_mode={exit_mode}  open_positions={len(open_rows)}\n")

    # Latest signals per (symbol, tf)
    latest_signals = {}
    for r in open_rows:
        latest_signals[(r["symbol"], int(r["tf_min"]))] = None

    sig_rows = conn.execute(
        """
        SELECT symbol, tf_min, kind, ts_ms, reason
        FROM signals
        WHERE (symbol, tf_min) IN (
            SELECT symbol, tf_min FROM trades WHERE status='open'
        )
        ORDER BY ts_ms DESC
        """
    ).fetchall()
    for sr in sig_rows:
        key = (sr["symbol"], int(sr["tf_min"]))
        if latest_signals.get(key) is None:
            latest_signals[key] = sr

    # Latest bars per (symbol, tf)
    latest_bars = {}
    for r in open_rows:
        latest_bars[(r["symbol"], int(r["tf_min"]))] = None

    bar_rows = conn.execute(
        """
        SELECT symbol, tf_min, start_ts_ms, end_ts_ms, low, high, close
        FROM bars
        WHERE (symbol, tf_min) IN (
            SELECT symbol, tf_min FROM trades WHERE status='open'
        )
        ORDER BY start_ts_ms DESC
        """
    ).fetchall()
    for br in bar_rows:
        key = (br["symbol"], int(br["tf_min"]))
        if latest_bars.get(key) is None:
            latest_bars[key] = br

    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    for r in open_rows:
        symbol = r["symbol"]
        tf = int(r["tf_min"])
        side = r["side"]
        qty = float(r["qty"])
        entry = float(r["entry_price"])
        opened = r["opened_ts_ms"]
        opened_dt = datetime.fromtimestamp(opened / 1000) if opened else None

        lb = latest_bars.get((symbol, tf))
        stop = None
        last_price = None
        stop_hit = None
        last_bar_time = None
        if lb is not None:
            last_price = float(lb["close"])
            last_bar_time = datetime.fromtimestamp(lb["end_ts_ms"] / 1000)
            if side == "long":
                stop = float(lb["low"])
                stop_hit = last_price <= stop
            else:
                stop = float(lb["high"])
                stop_hit = last_price >= stop

        ls = latest_signals.get((symbol, tf))
        last_kind = ls["kind"] if ls is not None else None
        last_kind_reason = (ls["reason"] or "") if ls is not None else ""

        mode = r["mode"] or "unknown"
        policy_hash = r["policy_hash"] or ""
        hold_minutes = (now_ts - opened) / 60000.0 if opened else 0.0

        # human readable "why not closed"
        why = []
        if stop_hit is False:
            why.append("стоп не пробит")
        elif stop_hit is True:
            why.append("стоп пробит (должно закрыться)")
        else:
            why.append("нет данных по стопу")

        if exit_mode == "hybrid":
            if last_kind == "flat":
                why.append("последний signal=FLAT (для hybrid должен закрыть)")
            else:
                why.append("signal не FLAT (для hybrid продолжает держать)")

        why_txt = "; ".join(why)

        print(
            f"- {symbol} tf={tf} {side} qty={qty} entry={entry:.6f} "
            f"opened={opened_dt} (~{hold_minutes:.1f} мин)"
        )
        print(
            f"  last_bar_close={last_price:.6f} last_bar_end={last_bar_time}  "
            f"trailing_stop={stop:.6f}  stop_hit={stop_hit}"
            if (last_price is not None and stop is not None)
            else "  trailing: n/a"
        )
        print(f"  last_signal_kind={last_kind} | {last_kind_reason[:90]}")
        print(f"  почему ещё не закрыто: {why_txt}")
        print(f"  entry_reason: {r['entry_reason'][:90] if r['entry_reason'] else ''}")
        print(f"  mode={mode} policy_hash={policy_hash[:10]}...\n")

    conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Trading bot SQLite report")
    parser.add_argument("--db", default=None, help="Path to bot.db")
    args = parser.parse_args(argv)
    settings = load_settings()
    db = args.db or settings.db_path
    print_report(db)


if __name__ == "__main__":
    main()
