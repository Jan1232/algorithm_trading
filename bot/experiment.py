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
    # Monkey gate (Davey ch.12) — frozen with window B economics pack
    require_monkey_pass: bool = True
    monkey_beat_threshold: float = 0.90
    monkey_runs: int = 2000
    monkey_seed: int = 42


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


def start_fresh_window_b(
    root: Path,
    *,
    policy_hash: str,
    strategy_label: str,
    criteria: Optional[PassCriteria] = None,
    notes: Optional[str] = None,
) -> ExperimentState:
    """
    Overwrite experiment.json for a new window B under a new policy_hash.
    Caller is responsible for wiping mode-specific SQLite DBs.
    """
    path = experiment_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    state = ExperimentState(
        policy_hash=policy_hash,
        strategy_label=strategy_label,
        frozen_at=now,
        window="B",
        window_a_started_at=now,
        window_b_started_at=now,
        criteria=criteria or PassCriteria(),
        notes=notes
        or (
            "Window B (economics pack): TF>=60, per_trade_risk_pct, "
            "vote>=2/margin>=2, trailing_buffer_frac; "
            "monkey gate (entry/exit/both ≥90% beat on Mo+maxDD, seed logged). "
            "Goal = economic viability of config, NOT factor attribution / edge. "
            "Do not add monkey gate mid-window — restart with new policy_hash."
        ),
    )
    path.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("experiment FRESH window B policy_hash=%s -> %s", policy_hash, path)
    return state


@dataclass(frozen=True)
class PassEvaluation:
    passed: bool
    reasons: tuple[str, ...]
    monkey_status: str = "skipped"

    def summary(self) -> str:
        flag = "PASS" if self.passed else "FAIL"
        body = "; ".join(self.reasons) if self.reasons else "ok"
        return f"{flag}: {body} (monkey={self.monkey_status})"


def evaluate_pass(
    *,
    trades_closed: int,
    mo: float,
    tf_baskets_positive_mo_pct: float,
    max_drawdown_pct: float,
    echelon2_block_rate: float,
    criteria: PassCriteria,
    monkey_status: str = "skipped",
) -> PassEvaluation:
    """
    Evaluate window pass criteria.

    ``monkey_status`` is one of: pass | fail | insufficient_data | skipped.
    Monkey FAIL blocks only when require_monkey_pass and trades_closed >= min.
    insufficient_data does NOT block (same as Mo — too noisy on small N).
    """
    reasons: list[str] = []
    if trades_closed < criteria.min_closed_trades:
        reasons.append(
            f"trades_closed={trades_closed} < min_closed_trades={criteria.min_closed_trades}"
        )
        # Early: Mo / monkey not decisive yet
        return PassEvaluation(
            passed=False,
            reasons=tuple(reasons),
            monkey_status="insufficient_data",
        )

    if criteria.require_positive_mo and mo <= 0:
        reasons.append(f"Mo={mo:.6f} <= 0")

    if tf_baskets_positive_mo_pct < criteria.min_tf_baskets_positive_mo_pct:
        reasons.append(
            f"tf_positive_mo_pct={tf_baskets_positive_mo_pct:.3f} "
            f"< {criteria.min_tf_baskets_positive_mo_pct:.3f}"
        )

    if max_drawdown_pct > criteria.max_drawdown_pct:
        reasons.append(
            f"max_drawdown_pct={max_drawdown_pct:.4f} > {criteria.max_drawdown_pct:.4f}"
        )

    if not (
        criteria.echelon2_block_rate_min
        <= echelon2_block_rate
        <= criteria.echelon2_block_rate_max
    ):
        reasons.append(
            f"echelon2_block_rate={echelon2_block_rate:.4f} "
            f"not in [{criteria.echelon2_block_rate_min}, {criteria.echelon2_block_rate_max}]"
        )

    effective_monkey = monkey_status
    if criteria.require_monkey_pass:
        if monkey_status == "fail":
            reasons.append("monkey gate FAIL (not beating random strategies)")
        elif monkey_status == "insufficient_data":
            # Should not happen when trades_closed >= min, but never block on it
            effective_monkey = "insufficient_data"
        elif monkey_status not in ("pass", "skipped"):
            reasons.append(f"monkey gate status={monkey_status}")
    else:
        effective_monkey = "skipped"

    return PassEvaluation(
        passed=len(reasons) == 0,
        reasons=tuple(reasons),
        monkey_status=effective_monkey,
    )
