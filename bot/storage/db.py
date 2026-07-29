from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from bot.models import Bar, ClosedTrade, Signal, VoteResult


class TradeStore:
    """SQLite store for bars, signals (with rule reasons), and trade results."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _columns(self, table: str) -> set[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}

    def _ensure_column(self, table: str, name: str, decl: str) -> None:
        if name not in self._columns(table):
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bars (
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

                CREATE TABLE IF NOT EXISTS shadow_bars (
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

                CREATE TABLE IF NOT EXISTS orderflow_bars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    tf_min INTEGER NOT NULL,
                    start_ts_ms INTEGER NOT NULL,
                    end_ts_ms INTEGER NOT NULL,
                    buy_vol REAL NOT NULL,
                    sell_vol REAL NOT NULL,
                    unknown_vol REAL NOT NULL,
                    delta REAL NOT NULL,
                    UNIQUE(symbol, tf_min, start_ts_ms)
                );

                CREATE TABLE IF NOT EXISTS footprint (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    tf_min INTEGER NOT NULL,
                    start_ts_ms INTEGER NOT NULL,
                    price_bucket REAL NOT NULL,
                    buy_vol REAL NOT NULL,
                    sell_vol REAL NOT NULL,
                    UNIQUE(symbol, tf_min, start_ts_ms, price_bucket)
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    tf_min INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    k REAL NOT NULL,
                    delta REAL NOT NULL,
                    bar_open REAL, bar_high REAL, bar_low REAL, bar_close REAL,
                    prev_open REAL, prev_high REAL, prev_low REAL, prev_close REAL,
                    reason TEXT NOT NULL,
                    checks_json TEXT NOT NULL,
                    vote_long INTEGER NOT NULL,
                    vote_short INTEGER NOT NULL,
                    vote_flat INTEGER NOT NULL,
                    vote_dominant TEXT NOT NULL,
                    mode TEXT
                );

                CREATE TABLE IF NOT EXISTS trades (
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

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    symbol TEXT,
                    tf_min INTEGER,
                    mode TEXT,
                    detail_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_bars_sym_tf ON bars(symbol, tf_min, start_ts_ms);
                CREATE INDEX IF NOT EXISTS idx_shadow_bars_sym_tf ON shadow_bars(symbol, tf_min, start_ts_ms);
                CREATE INDEX IF NOT EXISTS idx_orderflow_sym_tf ON orderflow_bars(symbol, tf_min, start_ts_ms);
                CREATE INDEX IF NOT EXISTS idx_footprint_sym_tf ON footprint(symbol, tf_min, start_ts_ms);
                CREATE INDEX IF NOT EXISTS idx_signals_sym ON signals(symbol, ts_ms);
                CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status, symbol);
                CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, ts_ms);
                """
            )
            # Migrate older DBs
            self._ensure_column("trades", "mode", "TEXT")
            self._ensure_column("trades", "fees_usd", "REAL")
            self._ensure_column("trades", "slippage_usd", "REAL")
            self._ensure_column("trades", "funding_usd", "REAL")
            self._ensure_column("trades", "policy_hash", "TEXT")
            self._ensure_column("signals", "mode", "TEXT")
            self._conn.commit()

    def save_bar(self, bar: Bar) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO bars(
                    symbol, tf_min, open, high, low, close,
                    start_ts_ms, end_ts_ms, tick_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bar.symbol,
                    bar.tf_min,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.start_ts_ms,
                    bar.end_ts_ms,
                    bar.tick_count,
                ),
            )
            self._conn.commit()

    def save_shadow_bar(self, bar: Bar) -> None:
        """Persist a non-trading diagnostic bar (1m/5m shadow collectors)."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO shadow_bars(
                    symbol, tf_min, open, high, low, close,
                    start_ts_ms, end_ts_ms, tick_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bar.symbol,
                    bar.tf_min,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.start_ts_ms,
                    bar.end_ts_ms,
                    bar.tick_count,
                ),
            )
            self._conn.commit()

    def save_orderflow_bar(self, bar: Any) -> None:
        """Persist aggressor volume bar (record-only; not used by trading)."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO orderflow_bars(
                    symbol, tf_min, start_ts_ms, end_ts_ms,
                    buy_vol, sell_vol, unknown_vol, delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bar.symbol,
                    bar.tf_min,
                    bar.start_ts_ms,
                    bar.end_ts_ms,
                    bar.buy_vol,
                    bar.sell_vol,
                    bar.unknown_vol,
                    bar.delta,
                ),
            )
            self._conn.commit()

    def save_footprint_rows(self, rows: list[dict]) -> None:
        """Persist footprint buckets for one closed order-flow bar."""
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO footprint(
                    symbol, tf_min, start_ts_ms, price_bucket, buy_vol, sell_vol
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["symbol"],
                        r["tf_min"],
                        r["start_ts_ms"],
                        r["price_bucket"],
                        r["buy_vol"],
                        r["sell_vol"],
                    )
                    for r in rows
                ],
            )
            self._conn.commit()

    def save_signal(
        self,
        signal: Signal,
        vote: VoteResult,
        *,
        mode: Optional[str] = None,
    ) -> int:
        prev = signal.prev_bar
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO signals(
                    ts_ms, symbol, tf_min, kind, k, delta,
                    bar_open, bar_high, bar_low, bar_close,
                    prev_open, prev_high, prev_low, prev_close,
                    reason, checks_json,
                    vote_long, vote_short, vote_flat, vote_dominant,
                    mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.bar.end_ts_ms,
                    signal.symbol,
                    signal.tf_min,
                    signal.kind.value,
                    signal.k,
                    signal.delta,
                    signal.bar.open,
                    signal.bar.high,
                    signal.bar.low,
                    signal.bar.close,
                    prev.open if prev else None,
                    prev.high if prev else None,
                    prev.low if prev else None,
                    prev.close if prev else None,
                    signal.reason,
                    json.dumps(signal.checks, ensure_ascii=False),
                    vote.n_long,
                    vote.n_short,
                    vote.n_flat,
                    vote.dominant.value,
                    mode,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def open_trade(
        self,
        *,
        symbol: str,
        tf_min: int,
        side: str,
        qty: float,
        entry_price: float,
        entry_reason: str,
        market: dict[str, Any],
        signal_id: Optional[int] = None,
        opened_ts_ms: Optional[int] = None,
        mode: Optional[str] = None,
        policy_hash: Optional[str] = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO trades(
                    symbol, tf_min, side, qty, entry_price,
                    opened_ts_ms, entry_reason, market_json, signal_id, status,
                    mode, policy_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    symbol,
                    tf_min,
                    side,
                    qty,
                    entry_price,
                    opened_ts_ms or int(time.time() * 1000),
                    entry_reason,
                    json.dumps(market, ensure_ascii=False),
                    signal_id,
                    mode,
                    policy_hash,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def close_trade(
        self,
        *,
        symbol: str,
        tf_min: int,
        trade: ClosedTrade,
        exit_reason: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE trades SET
                    exit_price = ?,
                    pnl = ?,
                    s = ?,
                    closed_ts_ms = ?,
                    exit_reason = ?,
                    fees_usd = ?,
                    slippage_usd = ?,
                    funding_usd = ?,
                    status = 'closed'
                WHERE id = (
                    SELECT id FROM trades
                    WHERE symbol = ? AND tf_min = ? AND status = 'open'
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (
                    trade.exit_price,
                    trade.pnl,
                    trade.s,
                    trade.closed_ts_ms or int(time.time() * 1000),
                    exit_reason,
                    trade.fees_usd,
                    trade.slippage_usd,
                    trade.funding_usd,
                    symbol,
                    tf_min,
                ),
            )
            self._conn.commit()

    def log_event(
        self,
        kind: str,
        *,
        symbol: Optional[str] = None,
        tf_min: Optional[int] = None,
        mode: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
        ts_ms: Optional[int] = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO events(ts_ms, kind, symbol, tf_min, mode, detail_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_ms or int(time.time() * 1000),
                    kind,
                    symbol,
                    tf_min,
                    mode,
                    json.dumps(detail or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            bars = self._conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
            signals = self._conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            open_n = self._conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status='open'"
            ).fetchone()[0]
            closed = self._conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status='closed'"
            ).fetchone()[0]
            pnl = self._conn.execute(
                "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='closed'"
            ).fetchone()[0]
            by_kind = self._conn.execute(
                "SELECT kind, COUNT(*) FROM signals GROUP BY kind"
            ).fetchall()
            by_mode = self._conn.execute(
                "SELECT COALESCE(mode,'unknown'), COUNT(*) FROM trades GROUP BY mode"
            ).fetchall()
            echelon2 = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind='echelon2_block'"
            ).fetchone()[0]
        return {
            "bars": bars,
            "signals": signals,
            "signals_by_kind": {row[0]: row[1] for row in by_kind},
            "trades_open": open_n,
            "trades_closed": closed,
            "realized_pnl": pnl,
            "trades_by_mode": {row[0]: row[1] for row in by_mode},
            "echelon2_blocks": echelon2,
        }

    def list_open_trades(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, symbol, tf_min, side, qty, entry_price, opened_ts_ms,
                       entry_reason, market_json, mode, policy_hash
                FROM trades WHERE status='open'
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def close_orphan_open(
        self,
        *,
        trade_id: int,
        reason: str = "sync_orphan",
    ) -> None:
        """Mark a stale open DB trade as closed (no longer on exchange)."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE trades SET
                    status='closed',
                    exit_reason=?,
                    closed_ts_ms=?,
                    pnl=COALESCE(pnl, 0),
                    s=COALESCE(s, 0)
                WHERE id=? AND status='open'
                """,
                (reason, int(time.time() * 1000), trade_id),
            )
            self._conn.commit()


    def close(self) -> None:
        with self._lock:
            self._conn.close()
