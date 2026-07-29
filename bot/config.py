from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

from bot.core.costs import CostModel
from bot.experiment import (
    ECHELON2_BLOCK_RATE_GATE_ENABLED_DEFAULT,
    MONKEY_BEAT_THRESHOLD_DEFAULT,
    MONKEY_RUNS_DEFAULT,
    MONKEY_SEED_DEFAULT,
    REQUIRE_MONKEY_PASS_DEFAULT,
)


@dataclass
class KillSwitchConfig:
    max_orders_per_minute: int = 30
    max_open_positions: int = 20
    max_daily_loss_pct: float = 0.05


@dataclass
class Settings:
    mode: str = "paper"
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    category: str = "linear"
    deposit_usd: float = 10_000.0
    deposit_from_wallet: bool = True
    max_drawdown_pct: float = 0.10
    tf_risk_pct: float = 0.10
    per_trade_risk_pct: float = 0.02
    max_leverage_frac: float = 1.0
    trailing_buffer_frac: float = 0.10
    vote_min_directional: int = 2
    vote_min_margin: int = 2
    tf_horizon_min: int = 1440
    tf_step_min: int = 15
    timeframes_explicit: list[int] = field(default_factory=list)
    min_ticks_per_sec: float = 0.1
    log_level: str = "INFO"
    paper_replay_ticks: int = 5000
    kill_switch: KillSwitchConfig = field(default_factory=KillSwitchConfig)
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    bybit_demo: bool = True
    bybit_testnet: bool = False
    db_path: str = "data/bot.db"
    db_path_paper: str = "data/bot_paper.db"
    db_path_demo: str = "data/bot_demo.db"
    db_path_live: str = "data/bot_live.db"
    one_position_per_symbol: bool = True
    reconcile_sec: float = 15.0
    costs: CostModel = field(default_factory=CostModel)
    strategy_label: str = "chebotarev_inspired_hybrid_exit"
    exit_mode: str = "hybrid"  # hybrid | book_close
    config_path: Optional[Path] = None

    @property
    def timeframes_min(self) -> list[int]:
        if self.timeframes_explicit:
            return list(self.timeframes_explicit)
        step = self.tf_step_min
        horizon = self.tf_horizon_min
        return list(range(step, horizon + 1, step))

    @property
    def max_drawdown_usd(self) -> float:
        """Absolute USD cap for echelon 2: P_fraction * deposit."""
        return self.deposit_usd * self.max_drawdown_pct

    def resolve_db_path(self) -> str:
        """Mode-specific SQLite so paper/demo/live histories stay separate."""
        if self.mode == "demo":
            return self.db_path_demo
        if self.mode == "live":
            return self.db_path_live
        if self.mode == "paper":
            return self.db_path_paper
        return self.db_path

    def frozen_policy_hash(self) -> str:
        payload = {
            "deposit_usd": self.deposit_usd,
            "max_drawdown_pct": self.max_drawdown_pct,
            "tf_risk_pct": self.tf_risk_pct,
            "per_trade_risk_pct": self.per_trade_risk_pct,
            "max_leverage_frac": self.max_leverage_frac,
            "trailing_buffer_frac": self.trailing_buffer_frac,
            "vote_min_directional": self.vote_min_directional,
            "vote_min_margin": self.vote_min_margin,
            "one_position_per_symbol": self.one_position_per_symbol,
            "symbols": self.symbols,
            "timeframes_min": self.timeframes_min,
            "taker_fee_bps": self.costs.taker_fee_bps,
            "slippage_bps": self.costs.slippage_bps,
            "funding_bps_per_8h": self.costs.funding_bps_per_8h,
            "strategy_label": self.strategy_label,
            "exit_mode": self.exit_mode,
            # PassCriteria / cost-model protocol knobs (window B).
            "require_monkey_pass": REQUIRE_MONKEY_PASS_DEFAULT,
            "monkey_beat_threshold": MONKEY_BEAT_THRESHOLD_DEFAULT,
            "monkey_runs": MONKEY_RUNS_DEFAULT,
            "monkey_seed": MONKEY_SEED_DEFAULT,
            "echelon2_block_rate_gate_enabled": ECHELON2_BLOCK_RATE_GATE_ENABLED_DEFAULT,
            # FIX-3: funding charged on entry (held) notional, not avg entry/exit
            "funding_notional_base": "entry",
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_settings(
    config_path: str | Path | None = None,
    env_path: str | Path | None = None,
) -> Settings:
    root = Path(__file__).resolve().parent.parent
    cfg_file = Path(config_path) if config_path else root / "config.yaml"
    load_dotenv(env_path or root / ".env")

    raw: dict[str, Any] = {}
    if cfg_file.exists():
        with cfg_file.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    ks_raw = raw.get("kill_switch") or {}
    kill_switch = KillSwitchConfig(
        max_orders_per_minute=int(ks_raw.get("max_orders_per_minute", 30)),
        max_open_positions=int(ks_raw.get("max_open_positions", 20)),
        max_daily_loss_pct=float(ks_raw.get("max_daily_loss_pct", 0.05)),
    )

    mode = str(os.getenv("BOT_MODE") or raw.get("mode", "paper")).lower()
    demo_env = os.getenv("BYBIT_DEMO")
    testnet_env = os.getenv("BYBIT_TESTNET")

    tfs = raw.get("timeframes_min")
    explicit = [int(x) for x in tfs] if isinstance(tfs, list) else []

    costs = CostModel(
        taker_fee_bps=float(raw.get("taker_fee_bps", 5.5)),
        slippage_bps=float(raw.get("slippage_bps", 5.0)),
        funding_bps_per_8h=float(raw.get("funding_bps_per_8h", 1.0)),
    )

    db_paper = str(raw.get("db_path_paper") or (root / "data" / "bot_paper.db"))
    db_demo = str(raw.get("db_path_demo") or (root / "data" / "bot_demo.db"))
    db_live = str(raw.get("db_path_live") or (root / "data" / "bot_live.db"))
    db_legacy = str(raw.get("db_path") or (root / "data" / "bot.db"))

    settings = Settings(
        mode=mode,
        symbols=list(raw.get("symbols") or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
        category=str(raw.get("category", "linear")),
        deposit_usd=float(raw.get("deposit_usd", 10_000.0)),
        deposit_from_wallet=bool(raw.get("deposit_from_wallet", True)),
        max_drawdown_pct=float(raw.get("max_drawdown_pct", 0.10)),
        tf_risk_pct=float(raw.get("tf_risk_pct", 0.10)),
        per_trade_risk_pct=float(raw.get("per_trade_risk_pct", 0.02)),
        max_leverage_frac=float(raw.get("max_leverage_frac", 1.0)),
        trailing_buffer_frac=float(raw.get("trailing_buffer_frac", 0.10)),
        vote_min_directional=int(raw.get("vote_min_directional", 2)),
        vote_min_margin=int(raw.get("vote_min_margin", 2)),
        tf_horizon_min=int(raw.get("tf_horizon_min", 1440)),
        tf_step_min=int(raw.get("tf_step_min", 15)),
        timeframes_explicit=explicit,
        min_ticks_per_sec=float(raw.get("min_ticks_per_sec", 0.1)),
        log_level=str(raw.get("log_level", "INFO")),
        paper_replay_ticks=int(raw.get("paper_replay_ticks", 5000)),
        kill_switch=kill_switch,
        bybit_api_key=os.getenv("BYBIT_API_KEY", ""),
        bybit_api_secret=os.getenv("BYBIT_API_SECRET", ""),
        bybit_demo=(demo_env.lower() == "true") if demo_env is not None else True,
        bybit_testnet=(testnet_env.lower() == "true") if testnet_env is not None else False,
        db_path=db_legacy,
        db_path_paper=db_paper,
        db_path_demo=db_demo,
        db_path_live=db_live,
        one_position_per_symbol=bool(raw.get("one_position_per_symbol", True)),
        reconcile_sec=float(raw.get("reconcile_sec", 15.0)),
        costs=costs,
        strategy_label=str(raw.get("strategy_label", "chebotarev_inspired_hybrid_exit")),
        exit_mode=str(raw.get("exit_mode", "hybrid")).lower(),
        config_path=cfg_file,
    )
    settings.db_path = settings.resolve_db_path()
    return settings
