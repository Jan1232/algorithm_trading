#!/usr/bin/env python3
"""Archive window B demo DB, wipe live counters, freeze fresh window C."""
from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bot.config import load_settings
from bot.experiment import start_fresh_window_c


def _export_sqlite(db_path: Path, out_dir: Path) -> dict:
    """Dump all tables to CSV + copy sqlite; return row counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, out_dir / db_path.name)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for table in tables:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        counts[table] = len(rows)
        path = out_dir / f"{table}.csv"
        if not rows:
            path.write_text("", encoding="utf-8")
            continue
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow({k: r[k] for k in r.keys()})
    conn.close()
    return counts


def main() -> None:
    root = _ROOT
    settings = load_settings()
    settings.mode = "demo"
    settings.db_path = settings.resolve_db_path()
    policy = settings.frozen_policy_hash()

    if settings.vote_min_margin != 1 or settings.vote_min_directional != 2:
        raise SystemExit(
            f"window C expects vote 2/1, got "
            f"{settings.vote_min_directional}/{settings.vote_min_margin}"
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = root / "data" / "archives" / f"window_b_{stamp}"
    archive.mkdir(parents=True, exist_ok=True)

    demo_db = Path(settings.db_path_demo)
    legacy = root / "data" / "bot_demo.db"
    src_db = demo_db if demo_db.exists() else (legacy if legacy.exists() else None)

    prev_exp = root / "data" / "experiment.json"
    window_b_started = None
    b_summary: dict = {}
    if prev_exp.exists():
        raw = json.loads(prev_exp.read_text(encoding="utf-8"))
        window_b_started = raw.get("window_b_started_at") or raw.get("frozen_at")
        shutil.copy2(prev_exp, archive / "experiment_window_b.json")
        b_summary = {
            "closed_policy_hash": raw.get("policy_hash"),
            "window": raw.get("window"),
            "frozen_at": raw.get("frozen_at"),
            "window_b_started_at": window_b_started,
        }

    if src_db is not None and src_db.exists():
        counts = _export_sqlite(src_db, archive)
        b_summary["table_counts"] = counts
        print(f"archived window B DB -> {archive} counts={counts}")
    else:
        print("no demo DB to archive")

    closure = {
        "closed_at_utc": datetime.now(timezone.utc).isoformat(),
        "verdict": (
            "Window B closed: config valid, TF grid works after restart fix, "
            "but entry tempo non-viable under vote 2/2 (~2 closed / ~34h). "
            "Mo unread (n=2). Successor = window C (vote_min_margin=1)."
        ),
        "window_b": b_summary,
        "window_c_policy_hash": policy,
        "archive_dir": str(archive.relative_to(root)).replace("\\", "/"),
    }
    (archive / "CLOSURE.json").write_text(
        json.dumps(closure, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (root / "data" / "window_b_closure.json").write_text(
        json.dumps(closure, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    for path in (demo_db, legacy, prev_exp):
        p = Path(path)
        if p.exists():
            p.unlink()
            print(f"removed {p}")

    state = start_fresh_window_c(
        root,
        policy_hash=policy,
        strategy_label=settings.strategy_label,
        window_b_started_at=window_b_started,
    )
    c = state.criteria
    print(
        f"window={state.window} policy_hash={state.policy_hash} "
        f"tfs={settings.timeframes_min} per_trade_risk={settings.per_trade_risk_pct} "
        f"vote={settings.vote_min_directional}/{settings.vote_min_margin} "
        f"trailing_buffer={settings.trailing_buffer_frac} "
        f"monkey={c.require_monkey_pass}@{c.monkey_beat_threshold}"
        f"/runs={c.monkey_runs}/seed={c.monkey_seed}"
    )


if __name__ == "__main__":
    main()
