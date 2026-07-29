# Протокол эксперимента (заморозка политики)

## Правила
1. При первом запуске создаётся `data/experiment.json` с `policy_hash`.
2. Любое изменение P, R, символов, TF, издержек, exit_mode, а также
   `per_trade_risk_pct` / `trailing_buffer_frac` / `vote_min_*` / `max_leverage_frac`
   → новый hash → старый эксперимент недействителен.
3. Окно A: plumbing + risk gates (не судить о edge).
4. Окно B: экономическая жизнеспособность конфигурации после costs; без подкрутки.

## Window B — economics pack (один policy_hash)

Состав пакета (факторы **не атрибутируются** по отдельности):
1. TF grid без 15/30: `[60, 120, 240, 480, 960, 1440]` — снижение оборота.
2. `per_trade_risk_pct=0.02` вместо `P / N_R` в сайзинге; `max_leverage_frac=1.0`.
3. `trailing_buffer_frac=0.10` — буфер trailing к range prev-бара.
4. Vote gate: `vote_min_directional=2`, `vote_min_margin=2`; `n_none` ≠ flat.

Цель window B: доказать, что конфигурация **экономически жизнеспособна**
(Mo>0 after costs при ≥200 закрытых сделках возможно). Не измерять edge
и не разносить вклад факторов. Walk-forward / изоляция факторов — после
первого жизнеспособного окна.

Сброс и старт B:
```bash
python scripts/start_window_b.py
```

## Критерии прохождения (по умолчанию в experiment.json)
- min_closed_trades >= 200
- Mo > 0 после fees/slippage/funding
- >= 70% TF-корзин с положительным Mo
- maxDD <= P
- доля блокировок echelon2 в [1%, 50%]

## Команды
```bash
python -m bot --report
python -m bot --cabinet
python scripts/start_window_b.py
```
