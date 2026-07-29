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

## Ограничения monkey test
1. Гейт только при `trades_closed >= min_closed_trades`.
2. Издержки обезьян = `CostModel` baseline (иначе сравнение нечестное).
3. Реплей по `close` баров — упрощение; цель = равные условия, не абсолютная точность.
4. Не доказывает устойчивость edge во времени.
5. PASS без записанного seed недействителен.

## Интерпретация режимов (entry / exit / both)

Это чтение уже существующего отчёта, не новый код:
- падает `monkey_exit`, `monkey_entry` проходит → edge во входе есть, проблема в выходе (hybrid/trailing);
- падает `monkey_entry`, `monkey_exit` проходит → проблема во входе (сигналы/vote);
- падает `monkey_both` при прохождении обоих одиночных → edge в комбинации/взаимодействии.

## Отложенные усиления monkey (после ≥200 сделок в окне)

Не внедрять до первого вердикта гейта:
1. **Per-TF monkey** (приоритет): PASS корзин ≥ порога при `min_trades_per_basket`; тонкие корзины = `insufficient_data`.
2. **Эмпирический hold** вместо mean (квантили p25/p50/p75 в отчёте).
3. **Walk-forward поверх monkey** — только после нескольких временных окон.

Cooldown / min-hold — отдельное ТЗ на торговое поведение, не часть monkey.

## Команды
```bash
python -m bot --report
python -m bot --cabinet
python -m bot --monkey [--runs 2000] [--seed 42] [--monkey-mode all|entry|exit|both]
python scripts/start_window_b.py
```
