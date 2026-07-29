from __future__ import annotations

import argparse
from pathlib import Path

from bot.config import load_settings
from bot.runtime.engine import Engine


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bybit trading robot (Chebotarev-inspired)")
    parser.add_argument("--mode", choices=["paper", "demo", "live"], default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--ticks",
        type=int,
        default=None,
        help="Paper synthetic replay tick count",
    )
    parser.add_argument(
        "--exit-mode",
        choices=["hybrid", "book_close"],
        default=None,
        help="Override exit logic",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print SQLite stats and exit",
    )
    parser.add_argument(
        "--cabinet",
        action="store_true",
        help="Show current open positions in human-readable form",
    )
    parser.add_argument(
        "--monkey",
        action="store_true",
        help="Run monkey test (random-strategy benchmark) and exit",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=2000,
        help="Monkey test iterations (default 2000)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Monkey test RNG seed (logged; required for reproducible PASS)",
    )
    parser.add_argument(
        "--monkey-mode",
        choices=["all", "entry", "exit", "both"],
        default="all",
        dest="monkey_mode",
        help="Monkey modes to run (default all)",
    )
    parser.add_argument(
        "--promote-b",
        action="store_true",
        help="Promote experiment from window A to B (one-shot)",
    )
    args = parser.parse_args(argv)

    settings = load_settings(config_path=args.config)
    if args.mode:
        settings.mode = args.mode
        settings.db_path = settings.resolve_db_path()
    if args.exit_mode:
        settings.exit_mode = args.exit_mode
        if args.exit_mode == "book_close":
            settings.strategy_label = "chebotarev_book_close_exit"
        else:
            settings.strategy_label = "chebotarev_inspired_hybrid_exit"
    paper_replay = False
    if args.ticks is not None:
        settings.paper_replay_ticks = args.ticks
        paper_replay = True

    if args.report:
        from bot.report import print_report

        print_report(settings.db_path)
        return

    if args.cabinet:
        from bot.report import print_cabinet

        print_cabinet(settings.db_path)
        return

    if args.monkey:
        from bot.analysis.monkey import ALL_MODES, print_monkey_report, run_monkey_test
        from bot.experiment import load_or_create_experiment

        root = Path(__file__).resolve().parent.parent
        policy = settings.frozen_policy_hash()
        state = load_or_create_experiment(
            root,
            policy_hash=policy,
            strategy_label=settings.strategy_label,
        )
        modes = ALL_MODES if args.monkey_mode == "all" else (args.monkey_mode,)
        beat = state.criteria.monkey_beat_threshold
        results = run_monkey_test(
            settings.db_path,
            policy_hash=state.policy_hash,
            costs=settings.costs,
            modes=modes,
            n_runs=args.runs,
            seed=args.seed,
            beat_threshold=beat,
        )
        out_json = root / "data" / "monkey_report.json"
        print_monkey_report(
            results,
            policy_hash=state.policy_hash,
            json_path=out_json,
        )
        print(f"wrote {out_json}")
        return

    if args.promote_b:
        from bot.experiment import load_or_create_experiment, promote_to_window_b

        root = Path(__file__).resolve().parent.parent
        state = load_or_create_experiment(
            root,
            policy_hash=settings.frozen_policy_hash(),
            strategy_label=settings.strategy_label,
        )
        promote_to_window_b(root, state)
        print(f"promoted to window B, policy_hash={state.policy_hash}")
        return

    Engine(settings, paper_replay=paper_replay).run()


if __name__ == "__main__":
    main()
