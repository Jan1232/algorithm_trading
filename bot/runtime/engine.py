from __future__ import annotations

import logging
import math
import random
import sys
import time
from typing import Optional

from bot.config import Settings, load_settings
from bot.core.risk import TfDrawdownTracker
from bot.core.validator import StabilityValidator
from bot.exchange.bybit_client import BybitClient
from bot.exchange.market_data import PublicTradeFeed
from bot.exchange.paper import PaperBroker
from bot.models import Tick
from bot.orders.killswitch import KillSwitch
from bot.orders.manager import OrderManager
from bot.runtime.portfolio import Portfolio
from bot.storage.db import TradeStore

logger = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    # Use stdout so PM2 puts INFO in out log, not error log
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def build_stack(settings: Settings):
    from pathlib import Path

    from bot.exchange.instruments import InstrumentRegistry
    from bot.experiment import load_or_create_experiment
    from bot.report import write_alert

    # bot/runtime/engine.py → project root
    root = Path(__file__).resolve().parents[2]
    # Ensure mode-specific DB (paper/demo/live never share bars+signals)
    settings.db_path = settings.resolve_db_path()
    tracker = TfDrawdownTracker()
    validator = StabilityValidator()
    kill = KillSwitch(
        max_orders_per_minute=settings.kill_switch.max_orders_per_minute,
        max_open_positions=settings.kill_switch.max_open_positions,
        max_daily_loss_pct=settings.kill_switch.max_daily_loss_pct,
        deposit=settings.deposit_usd,
    )
    store = TradeStore(settings.db_path)
    instruments = InstrumentRegistry()
    instruments.load_fallback(settings.symbols)

    policy = settings.frozen_policy_hash()
    exp = load_or_create_experiment(
        root,
        policy_hash=policy,
        strategy_label=settings.strategy_label,
    )
    logger.info(
        "sqlite=%s strategy=%s exit_mode=%s policy_hash=%s window=%s tfs=%s P_usd=%.2f",
        settings.db_path,
        settings.strategy_label,
        settings.exit_mode,
        policy,
        exp.window,
        settings.timeframes_min,
        settings.max_drawdown_usd,
    )

    def on_kill(reason: str) -> None:
        write_alert(root, "kill_switch", reason, {"policy_hash": policy})

    mode = settings.mode
    if mode == "live" and not (settings.bybit_api_key and settings.bybit_api_secret):
        raise SystemExit("live mode requires BYBIT_API_KEY/SECRET")

    if mode == "paper":
        broker = PaperBroker(costs=settings.costs)
    elif mode in ("demo", "live"):
        if not settings.bybit_api_key or not settings.bybit_api_secret:
            raise SystemExit(f"{mode} mode requires BYBIT_API_KEY and BYBIT_API_SECRET in .env")
        demo = mode == "demo" or settings.bybit_demo
        if mode == "live":
            demo = False
        broker = BybitClient(
            settings.bybit_api_key,
            settings.bybit_api_secret,
            demo=demo,
            testnet=settings.bybit_testnet if mode != "demo" else False,
            category=settings.category,
            instruments=instruments,
            costs=settings.costs,
        )
        broker.connect(symbols=settings.symbols)
        if settings.deposit_from_wallet:
            wallet = broker.get_wallet_usdt()
            if wallet is not None and wallet > 0:
                logger.info(
                    "deposit from Bybit wallet USDT equity=%.4f (config was %.4f)",
                    wallet,
                    settings.deposit_usd,
                )
                settings.deposit_usd = wallet
                kill.max_daily_loss_usd = wallet * settings.kill_switch.max_daily_loss_pct
            else:
                logger.warning(
                    "wallet USDT unavailable — using config deposit_usd=%.4f",
                    settings.deposit_usd,
                )
        broker.sync_positions_from_exchange(settings.symbols)
        # Re-bind tf_min=0 synced positions to open SQLite trades (same symbol+side)
        open_rows = store.list_open_trades()
        broker.rematch_tf_from_open_trades(open_rows)
        live_keys = {(p.symbol, p.side.value) for p in broker.get_positions()}
        for row in open_rows:
            key = (row["symbol"], row["side"])
            if key not in live_keys and row.get("id") is not None:
                store.close_orphan_open(trade_id=int(row["id"]), reason="sync_orphan")
                logger.warning(
                    "closed orphan DB open trade id=%s %s %s tf=%s (not on exchange)",
                    row["id"],
                    row["side"],
                    row["symbol"],
                    row["tf_min"],
                )
        # Push exchange SL for already-open positions
        for pos in broker.get_positions():
            if pos.stop_price > 0:
                broker.set_stop_loss(pos.symbol, pos.stop_price)
    else:
        raise SystemExit(f"unknown mode: {mode}")

    manager = OrderManager(
        broker,
        deposit=settings.deposit_usd,
        max_drawdown_usd=settings.max_drawdown_usd,
        tf_risk_pct=settings.tf_risk_pct,
        kill_switch=kill,
        validator=validator,
        tracker=tracker,
        store=store,
        exit_mode=settings.exit_mode,
        instruments=instruments,
        on_kill=on_kill,
        mode=mode,
        policy_hash=policy,
        one_position_per_symbol=settings.one_position_per_symbol,
        per_trade_risk_pct=settings.per_trade_risk_pct,
        max_leverage_frac=settings.max_leverage_frac,
        trailing_buffer_frac=settings.trailing_buffer_frac,
    )
    # If we rematched after a FLAT already fired, close those positions now
    if mode in ("demo", "live") and settings.exit_mode == "hybrid":
        manager.flush_flat_after_sync(store)
    portfolio = Portfolio(settings, manager, store=store)

    if mode == "paper" and isinstance(broker, PaperBroker):
        import json
        from bot.models import Position, Side

        for row in store.list_open_trades():
            market = {}
            try:
                market = json.loads(row.get("market_json") or "{}")
            except json.JSONDecodeError:
                pass
            protect = float(market.get("protect_stop") or row["entry_price"])
            pos = Position(
                symbol=row["symbol"],
                side=Side.LONG if row["side"] == "long" else Side.SHORT,
                qty=float(row["qty"]),
                entry_price=float(row["entry_price"]),
                tf_min=int(row["tf_min"]),
                stop_price=protect,
                opened_ts_ms=int(row["opened_ts_ms"] or 0),
            )
            broker.restore_position(pos)
            logger.info(
                "restored paper position %s tf=%s side=%s qty=%.6f @ %.4f stop=%.4f",
                pos.symbol,
                pos.tf_min,
                pos.side.value,
                pos.qty,
                pos.entry_price,
                pos.stop_price,
            )

    return broker, manager, portfolio, validator, kill, store

def generate_synthetic_ticks(
    symbols: list[str],
    n: int,
    *,
    start_ts_ms: Optional[int] = None,
    seed: int = 42,
) -> list[Tick]:
    """Generate ticks spanning enough time to close multiple TF bars."""
    rng = random.Random(seed)
    start = start_ts_ms or int(time.time() * 1000) - n * 5_000
    # ~5 seconds between ticks → 5000 ticks ≈ 7 hours
    prices = {s: 100.0 + i * 50 for i, s in enumerate(symbols)}
    ticks: list[Tick] = []
    for i in range(n):
        ts = start + i * 5_000
        for sym in symbols:
            # mild trending + noise so HH/HL patterns appear
            drift = 0.02 * math.sin(i / 40.0 + hash(sym) % 7)
            shock = rng.uniform(-0.5, 0.5)
            prices[sym] = max(1.0, prices[sym] * (1.0 + drift * 0.001) + shock)
            ticks.append(
                Tick(
                    symbol=sym,
                    price=round(prices[sym], 4),
                    size=rng.uniform(0.01, 0.5),
                    ts_ms=ts,
                )
            )
    ticks.sort(key=lambda t: t.ts_ms)
    return ticks


class Engine:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        paper_replay: bool = False,
    ) -> None:
        self.settings = settings or load_settings()
        self.paper_replay = paper_replay
        _setup_logging(self.settings.log_level)
        (
            self.broker,
            self.manager,
            self.portfolio,
            self.validator,
            self.kill,
            self.store,
        ) = build_stack(self.settings)
        self._tick_count = 0
        self._feed: Optional[PublicTradeFeed] = None

    def run_paper_replay(self) -> None:
        """One-shot synthetic ticks (for tests / smoke)."""
        logger.info(
            "paper replay symbols=%s tfs=%d ticks=%d",
            self.settings.symbols,
            len(self.settings.timeframes_min),
            self.settings.paper_replay_ticks,
        )
        ticks = generate_synthetic_ticks(
            self.settings.symbols,
            self.settings.paper_replay_ticks,
        )
        for tick in ticks:
            if self.kill.state.halted:
                logger.error("kill-switch halted: %s", self.kill.state.reason)
                break
            self.portfolio.on_tick(tick)

        report = self.validator.report()
        logger.info("stability: %s", report.summary())
        logger.info("db stats: %s", self.store.stats())
        positions = self.broker.get_positions()
        logger.info(
            "open positions=%d equity=%.4f",
            len(positions),
            getattr(self.broker, "equity", 0.0),
        )
        print(report.summary())

    def run_paper_live(self) -> None:
        """Continuous paper trading on live Bybit public trades (no real orders)."""
        assert isinstance(self.broker, PaperBroker)
        logger.info(
            "paper LIVE feed symbols=%s tfs=%d (virtual orders only)",
            self.settings.symbols,
            len(self.settings.timeframes_min),
        )

        def on_tick(tick: Tick) -> None:
            if self.kill.state.halted:
                return
            self._tick_count += 1
            self.portfolio.on_tick(tick)

        self._feed = PublicTradeFeed(
            testnet=False,
            on_tick=on_tick,
            symbols=self.settings.symbols,
        )
        self._feed.connect()
        self._feed.subscribe(self.settings.symbols)
        self._run_forever(close_feed=True)

    def run_live_loop(self) -> None:
        """Demo/live: PublicTradeFeed (with reconnect) + REST orders/reconcile."""
        assert isinstance(self.broker, BybitClient)

        def on_tick(tick: Tick) -> None:
            if self.kill.state.halted:
                return
            self._tick_count += 1
            self.broker.mark_price(tick.symbol, tick.price)
            self.portfolio.on_tick(tick)

        # Same reconnect path as paper — Bybit public linear trades
        self._feed = PublicTradeFeed(
            testnet=self.settings.bybit_testnet if self.settings.mode == "live" else False,
            on_tick=on_tick,
            symbols=self.settings.symbols,
        )
        self._feed.connect()
        self._feed.subscribe(self.settings.symbols)
        logger.info(
            "demo/live PublicTradeFeed + reconcile_sec=%.0f symbols=%s",
            self.settings.reconcile_sec,
            self.settings.symbols,
        )
        self._run_forever(close_feed=True, close_broker=True)

    def _run_forever(self, *, close_feed: bool = False, close_broker: bool = False) -> None:
        last_heartbeat = 0.0
        last_ws_check = 0.0
        last_reconcile = 0.0
        try:
            while True:
                if self.kill.state.halted:
                    logger.error("kill-switch halted: %s", self.kill.state.reason)
                    self.manager.emergency_stop()
                    break
                now = time.time()
                if self._feed is not None and now - last_ws_check >= 10.0:
                    last_ws_check = now
                    self._feed.ensure_alive(stale_sec=45.0)
                if (
                    isinstance(self.broker, BybitClient)
                    and now - last_reconcile >= self.settings.reconcile_sec
                ):
                    last_reconcile = now
                    try:
                        self.manager.reconcile()
                    except Exception:
                        logger.exception("reconcile failed")
                if now - last_heartbeat >= 60.0:
                    last_heartbeat = now
                    report = self.validator.report()
                    positions = self.broker.get_positions()
                    equity = getattr(self.broker, "equity", 0.0)
                    db = self.store.stats()
                    stale = ""
                    if self._feed is not None and self._feed.last_tick_ts > 0:
                        stale = f" ws_age={now - self._feed.last_tick_ts:.0f}s reconn={self._feed._reconnects}"
                    logger.info(
                        "heartbeat ticks=%d positions=%d equity=%.4f trades=%d Mo=%.6f "
                        "db_bars=%d db_signals=%s db_closed=%d pnl=%.4f%s",
                        self._tick_count,
                        len(positions),
                        equity,
                        report.n_trades,
                        report.mo,
                        db["bars"],
                        db["signals_by_kind"],
                        db["trades_closed"],
                        db["realized_pnl"],
                        stale,
                    )
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("interrupted")
        finally:
            report = self.validator.report()
            logger.info("stability: %s", report.summary())
            logger.info("db stats: %s", self.store.stats())
            print(report.summary())
            if close_feed and self._feed is not None:
                self._feed.close()
            if close_broker and isinstance(self.broker, BybitClient):
                self.broker.close()
            self.store.close()

    def run(self) -> None:
        mode = self.settings.mode
        if mode == "paper":
            if self.paper_replay:
                self.run_paper_replay()
            else:
                self.run_paper_live()
        elif mode in ("demo", "live"):
            if mode == "live":
                logger.warning("LIVE trading enabled — real funds at risk")
            self.run_live_loop()
        else:
            raise SystemExit(f"unknown mode {mode}")
