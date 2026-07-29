from pathlib import Path

from bot.core.signals import evaluate_signal
from bot.models import Bar, SignalKind
from bot.storage.db import TradeStore
from bot.models import VoteResult


def _bar(o, h, l, c, tf=15, start=0):
    return Bar("BTCUSDT", tf, o, h, l, c, start, start + tf * 60_000, 5)


def test_store_bar_signal_trade(tmp_path: Path):
    db = TradeStore(tmp_path / "t.db")
    prev = _bar(100, 105, 95, 102, start=0)
    cur = _bar(102, 120, 100, 118, start=15 * 60_000)
    sig = evaluate_signal(cur, prev)
    assert sig.kind == SignalKind.LONG
    assert "rule1" in sig.reason

    db.save_bar(cur)
    vote = VoteResult(n_long=1, n_short=0, n_flat=0, signals=[sig])
    sid = db.save_signal(sig, vote)
    assert sid > 0

    db.open_trade(
        symbol="BTCUSDT",
        tf_min=15,
        side="long",
        qty=1,
        entry_price=118,
        entry_reason=sig.reason,
        market={"checks": sig.checks},
        signal_id=sid,
    )
    from bot.models import ClosedTrade, Side

    trade = ClosedTrade("BTCUSDT", Side.LONG, 15, 1, 118, 125, 7, 0, 1)
    db.close_trade(symbol="BTCUSDT", tf_min=15, trade=trade, exit_reason="rule3_flat")
    st = db.stats()
    assert st["bars"] == 1
    assert st["signals"] == 1
    assert st["trades_closed"] == 1
    assert abs(st["realized_pnl"] - 7) < 1e-9
    db.close()
