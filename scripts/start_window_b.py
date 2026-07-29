#!/usr/bin/env python3
"""Reset demo DB + freeze a fresh experiment window B under current policy hash."""
from __future__ import annotations

from pathlib import Path

from bot.config import load_settings
from bot.experiment import start_fresh_window_b


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    settings = load_settings()
    settings.mode = "demo"
    settings.db_path = settings.resolve_db_path()
    policy = settings.frozen_policy_hash()

    for path in (
        root / settings.db_path_demo,
        root / "data" / "bot_demo.db",
        root / "data" / "experiment.json",
    ):
        p = Path(path)
        if p.exists():
            p.unlink()
            print(f"removed {p}")

    state = start_fresh_window_b(
        root,
        policy_hash=policy,
        strategy_label=settings.strategy_label,
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
