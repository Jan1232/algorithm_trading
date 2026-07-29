"""Experiment freeze: windows A/B, pass criteria, policy hash."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PassCriteria:
    min_closed_trades: int = 200
    require_positive_mo: bool = True
    min_tf_baskets_positive_mo_pct: float = 0.70
    max_drawdown_pct: float = 0.10
    echelon2_block_rate_min: float = 0.01
    echelon2_block_rate_max: float = 0.50


@dataclass
class ExperimentState:
    policy_hash: str
    strategy_label: str
    frozen_at: str
    window: str  # A | B
    window_a_started_at: str
    window_b_started_at: Optional[str] = None
    criteria: PassCriteria = field(default_factory=PassCriteria)
    notes: str = (
        "Window A: plumbing + risk gates only. "
        "Window B: look once for Mo sign after costs; no config changes."
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def experiment_path(root: Path) -> Path:
    return root / "data" / "experiment.json"


def load_or_create_experiment(
    root: Path,
    *,
    policy_hash: str,
    strategy_label: str,
    criteria: Optional[PassCriteria] = None,
) -> ExperimentState:
    path = experiment_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        crit_raw = raw.get("criteria") or {}
        state = ExperimentState(
            policy_hash=raw["policy_hash"],
            strategy_label=raw.get("strategy_label", strategy_label),
            frozen_at=raw["frozen_at"],
            window=raw.get("window", "A"),
            window_a_started_at=raw["window_a_started_at"],
            window_b_started_at=raw.get("window_b_started_at"),
            criteria=PassCriteria(**{**asdict(PassCriteria()), **crit_raw}),
            notes=raw.get("notes", ""),
        )
        if state.policy_hash != policy_hash:
            logger.error(
                "POLICY HASH MISMATCH: frozen=%s current=%s — "
                "changing config invalidates the experiment",
                state.policy_hash,
                policy_hash,
            )
        else:
            logger.info(
                "experiment loaded window=%s policy_hash=%s frozen_at=%s",
                state.window,
                state.policy_hash,
                state.frozen_at,
            )
        return state

    now = datetime.now(timezone.utc).isoformat()
    state = ExperimentState(
        policy_hash=policy_hash,
        strategy_label=strategy_label,
        frozen_at=now,
        window="A",
        window_a_started_at=now,
        criteria=criteria or PassCriteria(),
    )
    path.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("experiment FROZEN policy_hash=%s -> %s", policy_hash, path)
    return state


def promote_to_window_b(root: Path, state: ExperimentState) -> ExperimentState:
    now = datetime.now(timezone.utc).isoformat()
    state.window = "B"
    state.window_b_started_at = now
    path = experiment_path(root)
    path.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("experiment promoted to window B at %s", now)
    return state
