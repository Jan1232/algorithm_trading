#!/usr/bin/env python3
"""Generate review PDF for trader-mathematician evaluation."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "Review_Chebotarev_Bybit_Bot.pdf"
FONT = Path(r"C:\Windows\Fonts\arial.ttf")
FONT_B = Path(r"C:\Windows\Fonts\arialbd.ttf")


class PDF(FPDF):
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("ArialRu", size=9)
        self.set_text_color(100, 100, 100)
        self.set_x(self.l_margin)
        self.cell(
            0,
            6,
            "Обзор стратегии Чеботарёва / Bybit-бот — для рецензии",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(1)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("ArialRu", size=8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"{self.page_no()}/{{nb}}", align="C")

    def _ensure(self) -> None:
        self.set_x(self.l_margin)

    def h1(self, text: str) -> None:
        self._ensure()
        self.set_font("ArialRuBold", size=16)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 8, text)
        self.ln(2)

    def h2(self, text: str) -> None:
        self.ln(2)
        self._ensure()
        self.set_font("ArialRuBold", size=13)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 7, text)
        self.ln(1)

    def body(self, text: str) -> None:
        self._ensure()
        self.set_font("ArialRu", size=10)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 5.5, text)
        self.ln(1)

    def bullet(self, text: str) -> None:
        self._ensure()
        self.set_font("ArialRu", size=10)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 5.5, f"- {text}")

    def code(self, text: str) -> None:
        self._ensure()
        self.set_font("ArialRu", size=9)
        self.set_fill_color(245, 245, 245)
        self.set_text_color(10, 10, 10)
        self.multi_cell(0, 4.8, text, fill=True)
        self.ln(1)

    def note(self, text: str) -> None:
        self._ensure()
        self.set_font("ArialRu", size=9)
        self.set_text_color(80, 40, 0)
        self.set_fill_color(255, 248, 230)
        self.multi_cell(0, 5, text, fill=True)
        self.ln(1)


def build() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf = PDF(format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_font("ArialRu", fname=str(FONT))
    pdf.add_font("ArialRuBold", fname=str(FONT_B))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Title
    pdf.add_page()
    pdf.set_font("ArialRuBold", size=20)
    pdf.multi_cell(0, 10, "Торговый робот по Чеботарёву на Bybit")
    pdf.ln(2)
    pdf.set_font("ArialRu", size=12)
    pdf.multi_cell(
        0,
        6,
        "Документ для рецензии трейдера-математика / LLM-рецензента: "
        "оценка стратегии, математической постановки и программной реализации.",
    )
    pdf.ln(3)
    pdf.set_font("ArialRu", size=10)
    pdf.multi_cell(
        0,
        5.5,
        f"Дата сборки: {now}\n"
        "Источник стратегии: Ю. Чеботарёв «Торговые роботы на российском фондовом рынке» "
        "(инженерная выжимка: ТЗ_торговый_робот.md).\n"
        "Реализация: Python 3.12, Bybit API v5 (linear USDT perpetual), режимы paper / demo / live.\n"
        "Статус: paper LIVE на PM2 (виртуальные ордера, живые тики Bybit).",
    )

    pdf.h2("1. Запрос к рецензенту")
    pdf.body(
        "Просим дать общую оценку и ответить по пунктам. "
        "Синтетический replay — только иллюстрация пайплайна, не доказательство edge."
    )
    for q in [
        "Согласны ли вы с трактовкой рынка как нестационарного случайного ряда и запретом оптимизации параметров?",
        "Корректна ли безпараметрическая конструкция k и Delta из текущего бара? Есть ли смещение / look-ahead?",
        "Достаточна ли диверсификация по времени (15…1440 мин, шаг 15 → 96 копий) для снижения просадки на крипте 24/7?",
        "Корректно ли математически заданы эшелоны риска 1 и 2 и аллокация D/N_R?",
        "Является ли критерий Mo / масса плюс-минус по s адекватной валидацией (вместо бэктест-доходности)?",
        "Какие критические расхождения реализации с ТЗ снижают достоверность выводов?",
        "Что обязательно исправить до demo/live с реальными деньгами?",
        "Какой план статистической проверки на out-of-sample без оптимизации параметров?",
    ]:
        pdf.bullet(q)

    pdf.add_page()
    pdf.h1("2. Математическая постановка стратегии")
    pdf.h2("2.1. Допущения")
    for t in [
        "Рынок — случайная нестационарная последовательность цен; робот не прогнозирует тренд.",
        "Управляем только риском (макс. просадка депозита P), не ценой.",
        "Запрет оптимизации параметров по истории; система безпараметрическая.",
        "Нейросети запрещены для генерации торговых сигналов (переобучение).",
        "Алгоритм открытый и детерминированный.",
        "Единица данных — тик; бар OHLC на произвольном dt.",
    ]:
        pdf.bullet(t)

    pdf.h2("2.2. Ядро сигналов (правила 1–3)")
    pdf.body("Решение только на закрытии бара i относительно предыдущего бара i-1.")
    pdf.code(
        "Rule 1 LONG (все условия):\n"
        "  H_i > H_(i-1)\n"
        "  L_i > L_(i-1)\n"
        "  mid=(H_i+L_i)/2 > L_i + Delta\n\n"
        "Rule 2 SHORT (все условия):\n"
        "  L_i < L_(i-1)\n"
        "  H_i < H_(i-1)\n"
        "  mid < H_i - Delta\n\n"
        "Rule 3: иначе FLAT / выход\n\n"
        "k_long  = (C_i - O_i) / (H_i - L_i)  в [0,1]\n"
        "k_short = (O_i - C_i) / (H_i - L_i)  в [0,1]\n"
        "Delta   = (H_i - L_i) * (1 - k)\n"
        "k~0 боковик => Delta большая => вход блокируется\n"
        "k~1 импульс => Delta ~ 0"
    )
    pdf.note("Краевой случай H=L: в коде k:=0, вход блокируется (flat).")

    pdf.h2("2.3. Диверсификация по времени")
    pdf.body(
        "Книга (ММВБ): сессия 495 мин, шаг 15 → 33 TF. "
        "Крипто-адаптация: UTC-сутки 1440 мин, шаг 15 → 96 независимых копий на символ."
    )
    pdf.code(
        "N = N+ + N- + N0\n"
        "dominant = группа с наибольшим числом сигналов\n\n"
        "Allocator:\n"
        "  candidates = TF с сигналом dominant side\n"
        "  N_R = те, у кого drawdown_pct(TF) < R\n"
        "  notional = D / N_R  каждому"
    )

    pdf.h2("2.4. Риск: эшелоны 1 и 2")
    pdf.bullet(
        "Эшелон 1: long stop = Low предыдущего бара; short stop = High предыдущего бара; пробой → market exit."
    )
    pdf.bullet(
        "Эшелон 2: сумма потенциальных убытков по всем стопам >= -P, иначе новые входы запрещены."
    )
    pdf.code(
        "loss_long  = (stop - entry) * qty\n"
        "loss_short = (entry - stop) * qty\n"
        "allow_new = sum(loss) >= -P"
    )

    pdf.h2("2.5. Валидация")
    pdf.code(
        "s = p_sell - p_buy  (шорт: sell_short - buy_cover)\n"
        "Mo = P(win)*avg_win - P(loss)*avg_loss\n"
        "mass_ok <=> сумма положительных s > |сумма отрицательных s|"
    )

    pdf.add_page()
    pdf.h1("3. Реализация")
    pdf.h2("3.1. Стек")
    for t in [
        "Python 3.11+, пакет bot/",
        "Bybit v5 linear USDT perpetual; pybit",
        "Paper: PaperBroker + public WS trades; Demo: REST api-demo.bybit.com",
        "SQLite data/bot.db: bars, signals(+reason/checks), trades",
        "PM2 daemon + скрытый pythonw launcher",
        "SBProX вне исполнения (нет API робота)",
    ]:
        pdf.bullet(t)

    pdf.h2("3.2. Поток данных")
    pdf.code(
        "Tick (WS)\n"
        " -> TickBarBuilder для каждого tf в {15..1440 шаг 15}\n"
        " -> SignalCore правила 1-3 (+ reason/checks JSON)\n"
        " -> MultiTF vote N+/N-/N0\n"
        " -> Risk echelon2 + фильтр R\n"
        " -> Allocator D/N_R\n"
        " -> OrderManager -> PaperBroker | BybitClient\n"
        " -> TradeStore + StabilityValidator(Mo)"
    )

    pdf.h2("3.3. Инвесторские параметры")
    pdf.code(
        "deposit_usd=10000\n"
        "P=max_drawdown_pct=0.10\n"
        "R=tf_risk_pct=0.10\n"
        "symbols=BTCUSDT, ETHUSDT, SOLUSDT\n"
        "kill_switch: 30 ордеров/мин, 20 позиций, 5% дневной убыток"
    )

    pdf.h2("3.4. Соответствие ТЗ")
    for t in [
        "Тики→OHLC — Да (bars.py)",
        "Правила 1–3 + Delta — Да (signals.py)",
        "MTF + голосование — Да (mtf.py)",
        "Аллокатор D/N_R — Да (allocator.py)",
        "Эшелон 1/2 — Да (risk.py, manager.py)",
        "Kill-switch — Да, мягкий не-HFT (killswitch.py)",
        "Paper trading — Да, live ticks",
        "Валидатор Mo — Да (validator.py)",
        "Персистентность причин — Да (storage/db.py)",
        "Фильтр ликвидности — Частично (считается, жёстко не режет)",
        "Скальпинг §7.5 — Нет (вне MVP)",
        "Walk-forward на истории — Нет",
        "Точный qty step Bybit — Упрощённо (.3f)",
        "Private WS sync позиций — Нет в MVP",
    ]:
        pdf.bullet(t)

    pdf.add_page()
    pdf.h1("4. Расхождения и риски")
    pdf.h2("4.1. Логика vs книга")
    for t in [
        "Удержание «по close» из §3 смешано с ядром §5: выход через rule3 FLAT и trailing stop, а не отдельное сравнение close_i vs close_(i-1).",
        "При уже пробитой цене вход market вместо ожидания stop-limit fill.",
        "96 TF × 3 символа: нагрузка и возможная корреляция соседних TF.",
        "Крипто 24/7 vs сессия ММВБ: независимость TF не проверена.",
        "Paper fills без проскальзывания/комиссий → оптимистичный Mo.",
        "Synthetic replay на искусственных ценах (~100) — НЕ экстраполировать на BTC.",
    ]:
        pdf.bullet(t)

    pdf.h2("4.2. Инженерные риски")
    for t in [
        "Рестарт PM2 обнуляет in-memory бары/позиции; SQLite история есть, live-state не восстанавливается.",
        "Bybit demo: ордера REST; локальная модель позиций не полное зеркало биржи.",
        "Нет funding/комиссий в s и Mo.",
        "Kill-switch по дневному убытку может рано остановить paper на шуме.",
    ]:
        pdf.bullet(t)

    pdf.h1("5. Данные на момент сборки")
    pdf.h2("5.1. Live paper (data/bot.db)")
    pdf.body(
        "Файл есть, но bars=0, signals=0, trades=0. "
        "Причина: сигнал только после закрытия бара; мин. TF=15 мин, нужно >=2 бара "
        "(~30 мин после рестарта). PM2 online, тики идут."
    )
    pdf.note(
        "Отсутствие сделок сейчас — ожидаемое поведение, не отсутствие правил 1–3."
    )

    pdf.h2("5.2. Контрольный synthetic replay (data/review_sample.db)")
    pdf.body(
        "Прогон --ticks 3000 в отдельную БД. Цены синтетические. "
        "НЕ является оценкой edge стратегии."
    )
    pdf.code(
        "bars=108\n"
        "signals=108 (flat=59, long=23, short=26)\n"
        "trades_closed=11, trades_open=3\n"
        "realized_pnl≈3192.77 (синтетические единицы цены)\n"
        "P(win)=0.818 avg_win≈7.15 avg_loss≈0.86\n"
        "Mo≈5.69 mass_ok=True\n"
        "В логах: echelon2 blocked new entries — риск-гейт срабатывает."
    )

    pdf.h2("5.3. Формат причин в БД")
    pdf.code(
        "signals.reason: rule1 LONG / rule2 SHORT / rule3 FLAT + k, delta\n"
        "signals.checks_json: матрица неравенств + OHLC\n"
        "trades.entry_reason + market_json: vote, bar/prev, checks, stop\n"
        "trades.exit_reason: trailing_stop | rule3_flat | flip | emergency"
    )

    pdf.add_page()
    pdf.h1("6. Протокол оценки (без оптимизации параметров)")
    for t in [
        "Собрать достаточно закрытых paper-сделок на реальных тиках; зафиксировать Mo, mass_ok, maxDD.",
        "Сегментировать по корзинам TF; проверить устойчивость знака Mo.",
        "Доля блокировок echelon2/kill-switch — не over-constrained ли система.",
        "Стресс: комиссии + funding + slippage; порог смены знака Mo.",
        "Корреляция PnL соседних TF; при высоком rho — проредить сетку.",
        "После устойчивого paper — demo Bybit; сравнить fills со paper.",
        "Запрещено: grid-search по k/Delta/порогам. Допустимы P, R, символы, шаг TF как политика риска.",
    ]:
        pdf.bullet(t)

    pdf.h1("7. Резюме")
    pdf.body(
        "Реализован детерминированный каркас безпараметрической системы Чеботарёва с MTF, "
        "двумя эшелонами риска, paper на живых тиках Bybit и SQLite-журналом причин. "
        "Это инженерия контура, не доказанная прибыльность. "
        "Критично оценить: (1) Delta/k и look-ahead; (2) 96 TF на крипте; "
        "(3) расхождение exit с §3; (4) оптимизм paper fills; (5) план валидации Mo на реальных тиках."
    )
    pdf.note(
        "Оговорка книги и проекта: копирование схемы не гарантирует доходность; "
        "часть ноу-хау источника не раскрыта."
    )

    pdf.h2("Приложение. Ключевые файлы")
    for t in [
        "ТЗ_торговый_робот.md",
        "bot/core/signals.py, mtf.py, allocator.py, risk.py, validator.py",
        "bot/orders/manager.py, killswitch.py",
        "bot/exchange/paper.py, bybit_client.py, market_data.py",
        "bot/storage/db.py",
        "config.yaml, ecosystem.config.cjs, README.md",
        "data/bot.db (live), data/review_sample.db (synthetic)",
    ]:
        pdf.bullet(t)

    pdf.output(str(OUT))
    return OUT


if __name__ == "__main__":
    print(build())
