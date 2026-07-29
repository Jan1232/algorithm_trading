from __future__ import annotations

from dataclasses import dataclass

from bot.models import ClosedTrade


@dataclass(frozen=True)
class StabilityReport:
    n_trades: int
    n_wins: int
    n_losses: int
    p_win: float
    p_loss: float
    avg_win: float
    avg_loss: float
    mo: float
    sum_positive_s: float
    sum_negative_s: float
    mass_ok: bool

    def summary(self) -> str:
        return (
            f"trades={self.n_trades} wins={self.n_wins} losses={self.n_losses} "
            f"P(win)={self.p_win:.3f} avg_win={self.avg_win:.6f} "
            f"avg_loss={self.avg_loss:.6f} Mo={self.mo:.6f} "
            f"mass_ok={self.mass_ok}"
        )


class StabilityValidator:
    """Validate outcome distribution g(s), not past return curve."""

    def __init__(self) -> None:
        self.trades: list[ClosedTrade] = []

    def add(self, trade: ClosedTrade) -> None:
        self.trades.append(trade)

    def report(self) -> StabilityReport:
        if not self.trades:
            return StabilityReport(
                n_trades=0,
                n_wins=0,
                n_losses=0,
                p_win=0.0,
                p_loss=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                mo=0.0,
                sum_positive_s=0.0,
                sum_negative_s=0.0,
                mass_ok=False,
            )

        outcomes = [t.s for t in self.trades]
        wins = [s for s in outcomes if s > 0]
        losses = [s for s in outcomes if s <= 0]
        n = len(outcomes)
        n_wins = len(wins)
        n_losses = len(losses)
        p_win = n_wins / n
        p_loss = n_losses / n
        avg_win = sum(wins) / n_wins if n_wins else 0.0
        avg_loss = abs(sum(losses) / n_losses) if n_losses else 0.0
        mo = p_win * avg_win - p_loss * avg_loss
        sum_pos = sum(wins)
        sum_neg = abs(sum(losses))
        return StabilityReport(
            n_trades=n,
            n_wins=n_wins,
            n_losses=n_losses,
            p_win=p_win,
            p_loss=p_loss,
            avg_win=avg_win,
            avg_loss=avg_loss,
            mo=mo,
            sum_positive_s=sum_pos,
            sum_negative_s=sum_neg,
            mass_ok=sum_pos > sum_neg,
        )
