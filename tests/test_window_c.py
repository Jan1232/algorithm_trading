"""Window C: vote_min_margin=1 (NEW-HASH), keep directional≥2."""

from __future__ import annotations

from bot.config import Settings, load_settings
from bot.models import SignalKind, VoteResult

WINDOW_B_POLICY_HASH = "3eddb8d58eff91d6"
WINDOW_C_POLICY_HASH = "b38a2daf61e58795"


def _settings_like_frozen(**overrides) -> Settings:
    base = load_settings()
    kwargs = dict(
        deposit_usd=base.deposit_usd,
        max_drawdown_pct=base.max_drawdown_pct,
        tf_risk_pct=base.tf_risk_pct,
        per_trade_risk_pct=base.per_trade_risk_pct,
        max_leverage_frac=base.max_leverage_frac,
        trailing_buffer_frac=base.trailing_buffer_frac,
        vote_min_directional=base.vote_min_directional,
        vote_min_margin=base.vote_min_margin,
        one_position_per_symbol=base.one_position_per_symbol,
        symbols=list(base.symbols),
        timeframes_explicit=list(base.timeframes_min),
        costs=base.costs,
        strategy_label=base.strategy_label,
        exit_mode=base.exit_mode,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_window_c_hash_differs():
    h = load_settings().frozen_policy_hash()
    assert h == WINDOW_C_POLICY_HASH
    assert h != WINDOW_B_POLICY_HASH
    assert h == load_settings().frozen_policy_hash()  # deterministic
    b_like = _settings_like_frozen(vote_min_margin=2)
    assert b_like.frozen_policy_hash() == WINDOW_B_POLICY_HASH
    assert h == _settings_like_frozen(vote_min_margin=1).frozen_policy_hash()


def test_dominant_margin_1():
    # Was FLAT under margin=2; LONG under margin=1
    v = VoteResult(n_long=2, n_short=1, n_flat=3, n_none=0, min_directional=2, min_margin=1)
    assert v.dominant == SignalKind.LONG

    # No margin at all → still FLAT
    v2 = VoteResult(n_long=2, n_short=2, n_flat=2, n_none=0, min_directional=2, min_margin=1)
    assert v2.dominant == SignalKind.FLAT

    # directional gate still holds
    v3 = VoteResult(n_long=1, n_short=0, n_flat=5, n_none=0, min_directional=2, min_margin=1)
    assert v3.dominant == SignalKind.FLAT


def test_directional_gate_still_holds():
    v = VoteResult(n_long=1, n_short=0, n_flat=0, n_none=5, min_directional=2, min_margin=1)
    assert v.dominant == SignalKind.FLAT
    v_ok = VoteResult(n_long=2, n_short=0, n_flat=4, n_none=0, min_directional=2, min_margin=1)
    assert v_ok.dominant == SignalKind.LONG


def test_window_b_margin_2_behavior_preserved():
    """Old 2/2 gate still documented via explicit min_margin=2."""
    v4 = VoteResult(n_long=2, n_short=1, n_flat=3, n_none=0, min_directional=2, min_margin=2)
    assert v4.dominant == SignalKind.FLAT
    v5 = VoteResult(n_long=3, n_short=1, n_flat=2, n_none=0, min_directional=2, min_margin=2)
    assert v5.dominant == SignalKind.LONG
