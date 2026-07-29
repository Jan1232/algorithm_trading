"""Purge paper-era trades and tag remaining demo rows."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from bot.storage.db import TradeStore

# Local time when demo deposit=$100 / new policy started (UTC+5)
DEMO_CUTOFF = datetime(2026, 7, 28, 15, 3, 0)
POLICY = "c73d0194bbaca705"


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    db_path = root / "data" / "bot.db"
    store = TradeStore(db_path)  # migrates schema
    cutoff_ms = int(DEMO_CUTOFF.timestamp() * 1000)
    conn = store._conn

    before = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    paper = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE opened_ts_ms < ?", (cutoff_ms,)
    ).fetchone()[0]
    conn.execute("DELETE FROM trades WHERE opened_ts_ms < ?", (cutoff_ms,))
    # Tag remaining as demo (were untagged)
    conn.execute(
        "UPDATE trades SET mode='demo', policy_hash=? WHERE mode IS NULL OR mode=''",
        (POLICY,),
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    open_n = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status='open'"
    ).fetchone()[0]
    closed = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status='closed'"
    ).fetchone()[0]
    pnl = conn.execute(
        "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='closed'"
    ).fetchone()[0]
    print(
        f"purged paper trades={paper} (before={before} after={after}); "
        f"demo open={open_n} closed={closed} pnl={pnl:.4f}"
    )
    store.close()


if __name__ == "__main__":
    main()
