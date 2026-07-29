#!/usr/bin/env python3
"""
PDF-брифинг текущей ситуации demo-бота для думающей нейросети.

Выход: docs/Briefing_Demo_Status_For_LLM.pdf
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from bot.config import load_settings

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "Briefing_Demo_Status_For_LLM.pdf"
FONT = Path(r"C:\Windows\Fonts\arial.ttf")
FONT_B = Path(r"C:\Windows\Fonts\arialbd.ttf")


class PDF(FPDF):
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("ArialRu", size=8)
        self.set_text_color(110, 110, 110)
        self.set_x(self.l_margin)
        self.cell(
            0,
            5,
            "Briefing: Bybit demo bot (Chebotarev-inspired) — for LLM review",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(1)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("ArialRu", size=8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"{self.page_no()}/{{nb}}", align="C")

    def _e(self) -> None:
        self.set_x(self.l_margin)

    def h1(self, t: str) -> None:
        self._e()
        self.set_font("ArialRuBold", size=15)
        self.set_text_color(15, 15, 15)
        self.multi_cell(0, 7.5, t)
        self.ln(1.5)

    def h2(self, t: str) -> None:
        self.ln(1.5)
        self._e()
        self.set_font("ArialRuBold", size=12)
        self.set_text_color(25, 25, 25)
        self.multi_cell(0, 6.5, t)
        self.ln(0.8)

    def body(self, t: str) -> None:
        self._e()
        self.set_font("ArialRu", size=9.5)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 5.1, t)
        self.ln(0.6)

    def bullet(self, t: str) -> None:
        self._e()
        self.set_font("ArialRu", size=9.5)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 5.1, f"- {t}")

    def code(self, t: str) -> None:
        self._e()
        self.set_font("ArialRu", size=8)
        self.set_fill_color(245, 245, 245)
        self.set_text_color(10, 10, 10)
        self.multi_cell(0, 4.4, t, fill=True)
        self.ln(0.8)

    def note(self, t: str) -> None:
        self._e()
        self.set_font("ArialRu", size=9)
        self.set_text_color(70, 35, 0)
        self.set_fill_color(255, 248, 230)
        self.multi_cell(0, 4.8, t, fill=True)
        self.ln(1)


def _stats(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bars = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    sig = dict(conn.execute("SELECT kind, COUNT(*) FROM signals GROUP BY kind").fetchall())
    closed = list(
        conn.execute(
            """
            SELECT id, symbol, tf_min, side, qty, entry_price, exit_price, pnl, s,
                   entry_reason, exit_reason, fees_usd, slippage_usd, funding_usd,
                   opened_ts_ms, closed_ts_ms, market_json, mode, policy_hash
            FROM trades WHERE status='closed' ORDER BY id
            """
        )
    )
    opens = list(
        conn.execute(
            """
            SELECT id, symbol, tf_min, side, qty, entry_price, entry_reason,
                   opened_ts_ms, market_json, mode
            FROM trades WHERE status='open' ORDER BY id
            """
        )
    )
    fees = conn.execute(
        """
        SELECT COALESCE(SUM(fees_usd),0), COALESCE(SUM(slippage_usd),0),
               COALESCE(SUM(funding_usd),0), COALESCE(SUM(pnl),0)
        FROM trades WHERE status='closed'
        """
    ).fetchone()
    conn.close()

    wins = [r for r in closed if (r["pnl"] or 0) > 0]
    losses = [r for r in closed if (r["pnl"] or 0) <= 0]
    exits = Counter(r["exit_reason"] for r in closed)
    votes = Counter()
    holds = []
    rows_out = []
    for r in closed:
        m = {}
        try:
            m = json.loads(r["market_json"] or "{}")
        except json.JSONDecodeError:
            m = {}
        v = m.get("vote") or {}
        vote_key = f"L{v.get('long', 0)}S{v.get('short', 0)}F{v.get('flat', 0)}"
        votes[vote_key] += 1
        hold = 0.0
        if r["opened_ts_ms"] and r["closed_ts_ms"]:
            hold = (r["closed_ts_ms"] - r["opened_ts_ms"]) / 60_000
            holds.append(hold)
        rows_out.append(
            {
                "id": r["id"],
                "symbol": r["symbol"],
                "tf": r["tf_min"],
                "side": r["side"],
                "qty": r["qty"],
                "entry": r["entry_price"],
                "exit": r["exit_price"],
                "pnl": r["pnl"] or 0.0,
                "exit_reason": r["exit_reason"],
                "hold_min": hold,
                "vote": vote_key,
                "fees": r["fees_usd"] or 0.0,
                "slip": r["slippage_usd"] or 0.0,
            }
        )

    return {
        "bars": bars,
        "signals": sig,
        "closed": closed,
        "opens": opens,
        "rows": rows_out,
        "fees_sum": float(fees[0]),
        "slip_sum": float(fees[1]),
        "fund_sum": float(fees[2]),
        "pnl_sum": float(fees[3]),
        "wins": wins,
        "losses": losses,
        "exits": dict(exits),
        "votes": dict(votes),
        "hold_mean": (sum(holds) / len(holds)) if holds else 0.0,
        "hold_median": sorted(holds)[len(holds) // 2] if holds else 0.0,
    }


def build() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    settings.mode = "demo"
    settings.db_path = settings.resolve_db_path()
    st = _stats(Path(settings.db_path))

    exp_path = ROOT / "data" / "experiment.json"
    exp = json.loads(exp_path.read_text(encoding="utf-8")) if exp_path.exists() else {}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf = PDF(format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_font("ArialRu", fname=str(FONT))
    pdf.add_font("ArialRuBold", fname=str(FONT_B))

    # ---- PAGE 1: mission for the LLM ----
    pdf.add_page()
    pdf.set_x(pdf.l_margin)
    pdf.set_font("ArialRuBold", size=18)
    pdf.multi_cell(0, 9, "Briefing dlya dumayushchej nejroseti / Briefing for a thinking LLM")
    pdf.set_x(pdf.l_margin)
    pdf.set_font("ArialRuBold", size=14)
    pdf.multi_cell(0, 7, "Bybit demo bot (Chebotarev-inspired) — текущая ситуация")
    pdf.set_x(pdf.l_margin)
    pdf.set_font("ArialRu", size=10)
    pdf.multi_cell(0, 5.5, "Документ для внешней думающей модели: факты, статистика, узкие места, вопросы.")
    pdf.ln(1)
    pdf.note(
        f"Сгенерировано: {now}. Режим: DEMO (ордера на Bybit Demo Trading, не mainnet). "
        f"База: {settings.db_path}. Policy hash: {settings.frozen_policy_hash()}. "
        f"Эксперимент window={exp.get('window', '?')}, frozen_at={exp.get('frozen_at', '?')}."
    )

    pdf.h1("0. Задача для модели-рецензента")
    pdf.body(
        "Ты — думающая модель. Нужен жёсткий, конкретный разбор без общих слов. "
        "Не предлагай «добавить ML / больше индикаторов» без связи с данными ниже. "
        "Сфокусируйся на: (1) валидности edge при текущих издержках, (2) баге логики "
        "голосования MTF, (3) exit hybrid vs book, (4) что менять в policy, а что "
        "нельзя трогать до конца window A (заморозка)."
    )
    pdf.body("Вопросы, на которые желателен ответ:")
    pdf.bullet("Является ли текущий winrate ~12% ожидаемым шумом или системным дефектом входа?")
    pdf.bullet("Как правильно считать confirmation по TF, если flat доминирует (L1S0F7)?")
    pdf.bullet("Стоит ли запретить market already-through входы?")
    pdf.bullet("Какой минимальный порог голосов / фильтр до смены policy_hash?")
    pdf.bullet("При депозите ~$100 и taker+slip ~11 bps — есть ли смысл судить Mo раньше 200 сделок?")

    # ---- System ----
    pdf.h1("1. Что это за система")
    pdf.body(
        "Робот на Bybit linear USDT-perp (BTC/ETH/SOL). Стратегия вдохновлена "
        "Чеботарёвым (правила HH/HL / LL/LH + mid), но это НЕ буквальная книга: "
        "label = chebotarev_inspired_hybrid_exit."
    )
    pdf.bullet("Вход: stop-limit на пробой экстремума prev-бара; если цена уже через триггер → MARKET (already-through).")
    pdf.bullet("Выход hybrid: rule3 FLAT на TF позиции ИЛИ trailing stop по экстремуму prev-бара; на бирже ставится set_trading_stop (Full/Market SL).")
    pdf.bullet("Риск: deposit из UNIFIED wallet USDT; P=max_drawdown_pct=10%; R=tf_risk_pct=10%; one_position_per_symbol=true (один net на символ, полный риск на самый короткий eligible TF).")
    pdf.bullet("TF grid (заморожен): [15,30,60,120,240,480,960,1440] мин.")
    pdf.bullet("Издержки в учёте: taker 5.5 bps + slip 5.0 bps + funding 1 bps/8h (модель + запись fees/slip/funding в trades).")
    pdf.bullet("Инфра: PM2 bybit-demo-bot, PublicTradeFeed с ensure_alive, reconcile pending fills + exchange flats каждые 15с, отдельные DB paper/demo/live.")

    pdf.h2("Заморозка эксперимента")
    pdf.code(
        f"policy_hash={settings.frozen_policy_hash()}\n"
        f"window={exp.get('window')} frozen_at={exp.get('frozen_at')}\n"
        f"criteria: min_closed_trades>=200, Mo>0 after costs, "
        f">=70% TF baskets Mo>0, maxDD<=P, echelon2 block rate in [1%,50%]\n"
        f"Правило: менять P/R/symbols/TF/costs/exit_mode → новый hash → старый эксперимент недействителен."
    )

    # ---- Live stats ----
    pdf.h1("2. Текущая статистика (после clean slate)")
    n = len(st["closed"])
    nw, nl = len(st["wins"]), len(st["losses"])
    wr = (nw / n * 100) if n else 0.0
    avg_w = (sum(r["pnl"] for r in st["wins"]) / nw) if nw else 0.0
    avg_l = (sum(r["pnl"] for r in st["losses"]) / nl) if nl else 0.0
    pdf.body(
        f"Закрытых сделок: {n}. Открытых: {len(st['opens'])}. "
        f"Realized PnL: {st['pnl_sum']:+.4f} USD. "
        f"Wins/Losses: {nw}/{nl} (winrate {wr:.1f}%). "
        f"Avg win {avg_w:+.4f}, avg loss {avg_l:+.4f}. "
        f"Hold median {st['hold_median']:.1f} min, mean {st['hold_mean']:.1f} min."
    )
    pdf.body(
        f"Сигналы: {st['signals']} (bars={st['bars']}). "
        f"Сумма fees={st['fees_sum']:.4f}, slip={st['slip_sum']:.4f}, "
        f"funding={st['fund_sum']:.4f}; round-trip costs≈{st['fees_sum']+st['slip_sum']+st['fund_sum']:.4f} USD "
        f"при депозите config 100 / wallet ~99–100 USDT."
    )
    pdf.body(f"Exit reasons: {st['exits']}")
    pdf.body(f"Vote at entry (counts): {st['votes']}")

    if st["opens"]:
        pdf.h2("Открытая позиция")
        for o in st["opens"]:
            pdf.bullet(
                f"{o['side']} {o['symbol']} tf={o['tf_min']} qty={o['qty']} "
                f"entry={o['entry_price']} reason={o['entry_reason']}"
            )

    pdf.note(
        "Критично: типичный вход при голосе L1S0F7 или L0S1F7 — один TF направленный, "
        "семь FLAT. dominant() = argmax(n_long, n_short) и ИГНОРИРУЕТ flat majority. "
        "Это главный кандидат в «узкое горло» низкого winrate."
    )

    # ---- Bottlenecks ----
    pdf.h1("3. Узкие места (диагноз команды)")
    pdf.h2("A. Голосование MTF (главное)")
    pdf.body(
        "Формально multi-TF, фактически single-TF noise: вход при 1 directional vs 7 flat. "
        "one_position_per_symbol дополнительно выбирает кратчайший TF → максимум шума + оборот."
    )
    pdf.h2("B. Hybrid exit + короткий горизонт")
    pdf.body(
        "18/25 закрытий = rule3_flat; медиана удержания ~30 мин. Высокий оборот на 15m "
        "при taker+slip размывает любой слабый edge. Trailing по prev-bar extreme иногда "
        "выбивает за секунды (hold 0.2 min)."
    )
    pdf.h2("C. Market already-through")
    pdf.body(
        "Сигнал на close бара; если last уже за trigger — вход MARKET. Догоняем пробой, "
        "хуже fill, чаще сразу к стопу."
    )
    pdf.h2("D. Масштаб депозита")
    pdf.body(
        "На ~$100 BTC часто qty→0 (minQty 0.001) или 1 контракт; ETH/SOL торгуются, "
        "но costs/notional велики. Судить Mo до 200 сделок протокол запрещает (window A)."
    )
    pdf.h2("E. Уже починенные infra-баги (контекст)")
    pdf.bullet("Stop-limit fill tracking через reconcile get_open_orders/history.")
    pdf.bullet("Exchange SL via set_trading_stop; ghost-flat после биржевого SL без spam 110017.")
    pdf.bullet("PublicTradeFeed ensure_alive; отдельные bot_demo.db; deposit_from_wallet.")

    # ---- Trade table ----
    pdf.add_page()
    pdf.h1("4. Таблица закрытых сделок (все)")
    pdf.body(
        "Формат: id | symbol tf side | pnl | exit | hold_min | vote | fees+slip"
    )
    for r in st["rows"]:
        line = (
            f"#{r['id']} {r['symbol']} tf={r['tf']} {r['side']} "
            f"pnl={r['pnl']:+.4f} exit={r['exit_reason']} "
            f"hold={r['hold_min']:.1f}m vote={r['vote']} "
            f"cost={(r['fees']+r['slip']):.3f}"
        )
        pdf.code(line)

    # ---- Code-level facts ----
    pdf.h1("5. Факты реализации (для проверки гипотез)")
    pdf.code(
        "VoteResult.dominant: return LONG if n_long>n_short else SHORT if n_short>n_long else FLAT\n"
        "→ flat count не участвует в пороге входа.\n\n"
        "OrderManager.on_signals + one_position_per_symbol:\n"
        "→ eligible sorted by tf_min[:1] — полный риск на самый короткий TF.\n\n"
        "Entry: STOP_LIMIT else MARKET if price already through trigger.\n"
        "Exit hybrid: FLAT on position TF (or tf=0) OR soft trailing_stop; exchange SL pushed.\n\n"
        "Costs model: taker_fee_bps=5.5, slippage_bps=5.0, funding_bps_per_8h=1.0"
    )

    pdf.h1("6. Чего просим НЕ делать")
    pdf.bullet("Не предлагать live/mainnet.")
    pdf.bullet("Не менять policy «наугад» без явной гипотезы и понимания, что hash сбросит эксперимент.")
    pdf.bullet("Не оптимизировать Mo на выборке <<200 сделок (сейчас ~25).")
    pdf.bullet("Не игнорировать издержки: fees+slip уже ≈$4.4 при PnL ≈−$3.2.")

    pdf.h1("7. Желаемый формат ответа модели")
    pdf.bullet("Вердикт в 3–5 предложениях.")
    pdf.bullet("Ранжированный список дефектов (severity / secondary) с опорой на таблицу сделок.")
    pdf.bullet("Конкретные изменения правил (псевдокод порога голосов / запрет already-through / exit).")
    pdf.bullet("Что оставить в window A без изменений vs что осознанно ломает freeze.")
    pdf.bullet("Оценка: ожидаемый эффект на turnover и costs, не только на winrate.")

    pdf.ln(2)
    pdf.note(
        "Контакты артефактов: data/bot_demo.db, data/experiment.json, config.yaml, "
        "bot/orders/manager.py, bot/core/mtf.py, bot/exchange/bybit_client.py, "
        "docs/EXPERIMENT.md. Paper-бот отключён; работает только bybit-demo-bot."
    )

    pdf.output(str(OUT))
    return OUT


if __name__ == "__main__":
    path = build()
    print(path)
