# Торговый робот Bybit (ядро Чеботарёва — гибрид)

Безпараметрическое **ядро сигналов** по ТЗ из книги Ю. Чеботарёва (§5: правила 1–3, Δ из бара)
с исполнением через **Bybit API v5** (USDT perpetual / `linear`).

Честное именование: `strategy_label = chebotarev_inspired_hybrid_exit` —
выход сейчас через **rule3 FLAT + trailing stop**, а не отдельное книжное правило
«держать пока close монотонен» (§3). Это гибрид, вдохновлённый книгой, не буквальная копия.

SBProX в контур исполнения **не входит** (нет API для внешних роботов) — его можно использовать только для ручного анализа.

## Возможности MVP

- Сборка OHLC-баров из тиков на произвольном `dt`
- Сигналы правил 1–3 с `Δ` из текущего бара (без оптимизации)
- Мультитаймфрейм (15…1440 мин, шаг 15) + голосование
- Распределение депозита `D / N_R` с отсечением по риску `R`
- Скользящий стоп (эшелон 1) + глобальный лимит просадки `P` (эшелон 2)
- Kill-switch (частота заявок, число позиций, дневной убыток)
- Режимы: `paper` | `demo` | `live`
- Валидатор устойчивости: `Mo`, масса плюс/минус по `s`

## Установка

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
```

Скопируйте `.env.example` → `.env` и заполните ключи для demo/live.

## Конфиг

Основные параметры инвестора в `config.yaml`:

- `deposit_usd`, `max_drawdown_pct` (`P`), `tf_risk_pct` (`R`)
- `symbols`, `tf_horizon_min`, `tf_step_min`
- лимиты `kill_switch`

## Запуск

### Paper (виртуальные ордера)

По умолчанию paper слушает **живые** публичные тики Bybit и торгует виртуально (ордера на биржу не уходят):

```bash
python -m bot --mode paper
```

Разовый синтетический прогон (smoke/tests):

```bash
python -m bot --mode paper --ticks 1000
```

Фоново через PM2:

```bash
npm install -g pm2
mkdir logs
pm2 start ecosystem.config.cjs
pm2 status
pm2 logs bybit-paper-bot
pm2 stop bybit-paper-bot
```

### Bybit Demo

1. Войдите на Bybit (mainnet) → переключитесь в **Demo Trading**
2. Создайте API key **внутри demo-аккаунта**
3. Пропишите в `.env`:

```
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_DEMO=true
```

4. Запуск:

```bash
python -m bot --mode demo
```

Ордера идут REST на `api-demo.bybit.com`. Публичные тики — с mainnet stream. WS Trade на demo недоступен.

### Live

Только осознанно: в `.env` ключи боевого аккаунта, `BYBIT_DEMO=false`, затем:

```bash
python -m bot --mode live
```

### Отчёт по БД

```bash
python -m bot --report
```

### Окна эксперимента A/B

При первом запуске создаётся `data/experiment.json` с `policy_hash`.
Менять конфиг после старта = новый эксперимент.

```bash
python -m bot --promote-b   # перейти к окну B (смотреть Mo один раз)
```

### Режим выхода

```bash
python -m bot --mode paper --exit-mode hybrid      # rule3 + trailing (по умолчанию)
python -m bot --mode paper --exit-mode book_close  # §3: выход при развороте close
```

## База статистики

SQLite-файл `data/bot.db` (путь в `config.yaml` → `db_path`):

| Таблица | Что хранит |
|---|---|
| `bars` | закрытые OHLC-бары (состояние рынка) |
| `signals` | решение правил 1–3 + `reason` + JSON проверок + голосование TF |
| `trades` | вход (причина + снимок рынка) и выход (pnl, s, причина) |

Пример:

```bash
python -c "from bot.storage.db import TradeStore; print(TradeStore('data/bot.db').stats())"
```

## Структура

```
bot/
  core/       # бары, сигналы, MTF, риск, аллокатор, валидатор
  storage/    # SQLite
  exchange/   # paper + bybit
  orders/     # менеджер ордеров, kill-switch
  runtime/    # portfolio + engine
```

## Важно

Схема из ТЗ — рабочий каркас, **не гарантия прибыли**. Сначала paper, затем длительная обкатка на Demo. Параметры стратегии по истории не оптимизируются; задаются только лимиты риска инвестора.
