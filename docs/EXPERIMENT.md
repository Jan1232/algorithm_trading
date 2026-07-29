# Протокол эксперимента (заморозка политики)

## Правила
1. При первом запуске создаётся `data/experiment.json` с `policy_hash`.
2. Любое изменение P, R, символов, TF, издержек, exit_mode, а также
   `per_trade_risk_pct` / `trailing_buffer_frac` / `vote_min_*` / `max_leverage_frac`
   / `deposit_usd` (fallback) / funding-модели → новый hash → старый эксперимент
   недействителен.
3. Окно A: plumbing + risk gates (не судить о edge).
4. Окно B: экономическая жизнеспособность конфигурации после costs; без подкрутки.

## Window B — economics pack (один policy_hash)

Текущий freeze id: **`policy_hash=3eddb8d58eff91d6`**.

Состав пакета (факторы **не атрибутируются** по отдельности):
1. TF grid без 15/30: `[60, 120, 240, 480, 960, 1440]` — снижение оборота.
2. `per_trade_risk_pct=0.02` вместо `P / N_R` в сайзинге; `max_leverage_frac=1.0`.
3. `trailing_buffer_frac=0.10` — **round-1 constant, not data-calibrated**;
   revisit if `monkey_exit` fails.
4. Vote gate: `vote_min_directional=2`, `vote_min_margin=2`; `n_none` ≠ flat.
5. **Monkey gate** (Davey ch.12): baseline должен бить ≥90% случайных аналогов
   по Mo **и** maxDD во всех трёх режимах (`entry` / `exit` / `both`).
   Seed фиксируется и пишется в отчёт. Гейт применяется только при
   `trades_closed >= min_closed_trades` (иначе вердикт — шум, не FAIL).
6. `deposit_usd` fallback = **1000** (demo/live берут wallet; wallet должен быть
   ≥ ~$1000, иначе BTC может не торговаться из‑за minQty).
7. Funding считается от **entry (held) notional** × `hold_hours/8`, не от среднего
   entry/exit.

Цель window B: доказать, что конфигурация **экономически жизнеспособна**
и отличима от случайного входа/выхода на той же статистике сделок.
Не измерять устойчивость edge во времени (walk-forward — отдельно).

`Mo > 0` само по себе **не** доказывает edge: случайный вход с той же частотой,
направлением и удержанием мог бы дать такой же Mo. Monkey test отвечает:
«дал бы случайный вход Mo не хуже — и как часто?»

Сброс и старт B:
```bash
python scripts/start_window_b.py
```

Не добавлять monkey-гейт к уже идущему окну задним числом — только вместе
с новым `policy_hash` / `start_fresh_window_b`.

## Критерии прохождения (по умолчанию в experiment.json)
- min_closed_trades >= 200
- Mo > 0 после fees/slippage/funding
- >= 70% TF-корзин с положительным Mo
- maxDD <= P (рантайм-предохранитель echelon-2; P=0.10 остаётся)
- monkey gate PASS (entry+exit+both), seed logged; порог 0.90; runs=2000

**Исключено из pass-критериев:** доля блокировок echelon2 ∈ [1%, 50%].
После `per_trade_risk_pct` block rate структурно ≈ 0% и больше не информативен.
Поле `echelon2_block_rate_gate_enabled=false` (логирование полей сохранено).

Calmar ratio в `--report` — **только informational**, не pass-criterion.

## Ограничения monkey test
1. Гейт только при `trades_closed >= min_closed_trades`.
2. Издержки обезьян = `CostModel` baseline (иначе сравнение нечестное).
3. Реплей по `close` баров — упрощение; цель = равные условия, не абсолютная точность.
4. Не доказывает устойчивость edge во времени.
5. PASS без записанного seed недействителен.
6. Baseline-сделки из journal (live-путь), НЕ параллельный SignalCore-реплей.
7. `monkey_exit` сохраняет реальный `entry_price` baseline; варьируется только выход.

## Интерпретация режимов (entry / exit / both)

Это чтение уже существующего отчёта, не новый код:
- падает `monkey_exit`, `monkey_entry` проходит → edge во входе есть, проблема в выходе (hybrid/trailing);
- падает `monkey_entry`, `monkey_exit` проходит → проблема во входе (сигналы/vote);
- падает `monkey_both` при прохождении обоих одиночных → edge в комбинации/взаимодействии.

## Momentum-диагностика (NO-HASH, сейчас)

`python -m bot --momentum-diag` → Hurst (log-returns R/S) + Variance Ratio +
лаговая corr на неперекрывающихся окнах по каждой ячейке symbol×tf.
Результат — `data/momentum_diag.json`. **Не меняет config / hash.**

Теневой сбор коротких ТФ (record-only): `shadow_timeframes_min: [1, 5]` пишет
в `shadow_bars` (не в `bars`), без сигналов/ордеров. Диагностика:
`python -m bot --momentum-diag --shadow` → `data/momentum_diag_shadow.json`
+ costs-арифметика (median move bps / RT cost bps). **НЕ в policy_hash.**
Живую торговлю на 1/5m не запускать, пока диагностика + costs не зелёные;
это вход для будущего `[NEW-HASH]` окна, не для текущего window B.

Перекрёстная валидация с monkey:
- Hurst<0.5 + fail `monkey_entry` → согласованное свидетельство против bar-momentum;
- Hurst>0.5 + PASS `monkey_entry` → предпосылка подтверждена независимо от Chan.

## POST-200 / NEW-HASH (не трогать до ≥200 сделок + вердикта гейта)

Зафиксированные методы/гипотезы (чтобы не потерять и не обсуждать заново):

1. **H-exit** (триггер: fail `monkey_exit`): в отдельном окне сравнить
   - H-exit-A: калибровка `trailing_buffer_frac` от баров;
   - H-exit-B: убрать trailing, выход только rule3 FLAT (`book_close` / `rule3_only`).
   Обе `[NEW-HASH]`.
2. **Kill-порог** `max_daily_loss_pct`: калибровка от эмпирических дневных ходов
   (учёт kurtosis крипты), между окнами, не внутри замера.
3. **Реакция на momentum-diag**: срез TF / смена класса (MR или order-flow) —
   только по данным, отдельные окна `[NEW-HASH]`.
4. **Order flow edge** — дальний горизонт, если bar-momentum мёртв.
5. **Усиления monkey**: per-TF monkey; эмпирический hold (p25/p50/p75);
   walk-forward + t-тест OOS-vs-IS; Calmar≥1 как приёмка на стадии live;
   Kelly/half-Kelly как верхняя граница плеча (не таргет; без constant-leverage
   rebalancing на непроверенном edge).

Отклонено сейчас: ARIMA/Bollinger/OU (MR-класс), ML на midprice, Kelly/Markowitz
аллокация на <200 сделок, cross-exchange arb, news-momentum.

## Команды
```bash
python -m bot --report
python -m bot --cabinet
python -m bot --monkey [--runs 2000] [--seed 42] [--monkey-mode all|entry|exit|both]
python -m bot --momentum-diag
python -m bot --momentum-diag --shadow
python scripts/start_window_b.py
```
