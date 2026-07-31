# Протокол эксперимента (заморозка политики)

## Правила
1. При первом запуске создаётся `data/experiment.json` с `policy_hash`.
2. Любое изменение P, R, символов, TF, издержек, exit_mode, а также
   `per_trade_risk_pct` / `trailing_buffer_frac` / `vote_min_*` / `max_leverage_frac`
   / `deposit_usd` (fallback) / funding-модели → новый hash → старый эксперимент
   недействителен.
3. Окно A: plumbing + risk gates (не судить о edge).
4. Окно B: economics pack при vote 2/2 — **закрыто** (темп нежизнеспособен).
5. Окно C: тот же pack, `vote_min_margin=1` (сохраняя `vote_min_directional=2`).

## Window B — CLOSED (hash `3eddb8d58eff91d6`)

Заморожено: `2026-07-29T13:52:29Z`. Артефакты: `data/archives/window_b_*`,
`data/window_b_closure.json`.

Состав пакета (без изменений переносится в C, кроме margin):
1. TF grid: `[60, 120, 240, 480, 960, 1440]`.
2. `per_trade_risk_pct=0.02`; `max_leverage_frac=1.0`.
3. `trailing_buffer_frac=0.10` — round-1 constant.
4. Vote: `vote_min_directional=2`, **`vote_min_margin=2`**.
5. Monkey gate ≥90% beat (entry/exit/both), seed logged; ≥200 closed.
6. `deposit_usd` fallback = 1000; funding на entry notional.
7. Restart-recovery (NO-HASH, `1a936f0`): prev из БД; partial kline optional OFF.

**Вердикт закрытия (не по Mo):** конфигурация валидна, грид после фикса
рестарта работает (бары до 960), но темп входов структурно нежизнеспособен
(~2 закрытых / ~34 ч → месяцы до гейта 200). Directional-сигналы были
(long+short), vote 2/2 резал ~94%. **Mo / PnL window B не читаются (n=2).**
Сделки B **не** входят в PassCriteria окна C (другой hash + свежая demo DB).

## Window C — vote 2/1 (текущее)

Текущий freeze id: **`policy_hash=b38a2daf61e58795`**.

Единственное отличие от B: `vote_min_margin: 1` (было 2).
`vote_min_directional` остаётся **2** — один шумный ТФ по-прежнему не входит.

Цель: проверить, лечится ли темп ослаблением margin без потери качества входа.
**monkey_entry обязателен и приоритетен** при ≥200: baseline 2/1 vs случайный
вход. Если не бьёт random — купили частоту ценой мусора; тогда margin=2 был
фильтром качества, а не killer'ом, и нужен другой путь (не ослабление vote).

Сброс и старт C:
```bash
# остановить demo PM2, затем:
python scripts/start_window_c.py
pm2 restart bybit-demo-bot
```

Не читать Mo раньше 200 закрытых. Не менять config mid-window.

## Критерии прохождения (PassCriteria — те же числа)
- min_closed_trades >= 200
- Mo > 0 после fees/slippage/funding
- >= 70% TF-корзин с положительным Mo
- maxDD <= P
- monkey gate PASS (entry+exit+both); на C особенно смотреть **monkey_entry**
- echelon2 block-rate gate **выключен**

Calmar в `--report` — informational only.

## Momentum / shadow / orderflow (NO-HASH)

Без изменений относительно B: record-only, не в hash, не в сигнал.
Шаг 2 order-flow vs Mo — после накопления закрытых сделок **текущего** окна.

## POST-200 / NEW-HASH (после вердикта гейта C)

1. **H-exit** (триггер: fail `monkey_exit`).
2. Kill-порог `max_daily_loss_pct`.
3. Реакция на momentum-diag / смена класса.
4. Order flow edge — дальний горизонт.
5. Усиления monkey (per-TF, walk-forward, …).
6. **Window D** — только если темп C всё ещё низкий или monkey_entry FAIL;
   не планировать заранее ослабление `vote_min_directional` до 1.

Отклонено сейчас: ARIMA/Bollinger/OU, ML на midprice, Kelly/Markowitz на <200,
cross-exchange arb, news-momentum.

## Команды
```bash
python -m bot --report
python -m bot --cabinet
python -m bot --monkey [--runs 2000] [--seed 42] [--monkey-mode all|entry|exit|both]
python -m bot --momentum-diag
python -m bot --momentum-diag --shadow
python scripts/start_window_c.py
```
