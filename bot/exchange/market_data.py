from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from bot.models import Tick

logger = logging.getLogger(__name__)

# Native Bybit v5 kline intervals for our TFs.
# 480 / 960 are NOT supported by Bybit — Part B skips those (ticks only).
BYBIT_KLINE_INTERVAL: dict[int, str] = {
    1: "1",
    3: "3",
    5: "5",
    15: "15",
    30: "30",
    60: "60",
    120: "120",
    240: "240",
    360: "360",
    720: "720",
    1440: "D",
}


def tf_to_bybit_interval(tf_min: int) -> Optional[str]:
    return BYBIT_KLINE_INTERVAL.get(tf_min)


def fetch_current_kline(
    symbol: str,
    tf_min: int,
    *,
    category: str = "linear",
    testnet: bool = False,
) -> Optional[dict]:
    """
    Public REST current (forming) kline for approximate partial-bar restore.
    Returns dict with open/high/low/close/start_ts_ms, or None if TF unsupported / error.
    """
    interval = tf_to_bybit_interval(tf_min)
    if interval is None:
        return None
    try:
        from pybit.unified_trading import HTTP

        http = HTTP(testnet=testnet)
        resp = http.get_kline(
            category=category,
            symbol=symbol,
            interval=interval,
            limit=2,
        )
        rows = ((resp.get("result") or {}).get("list")) or []
        if not rows:
            return None
        # Bybit returns newest first; [0] is the currently forming candle.
        row = rows[0]
        return {
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "start_ts_ms": int(row[0]),
        }
    except Exception:
        logger.exception(
            "fetch_current_kline failed symbol=%s tf=%s", symbol, tf_min
        )
        return None


class PublicTradeFeed:
    """
    Bybit public trade WebSocket with reconnect.
    If the socket dies (ping/pong timeout), resubscribe after a short backoff.
    """

    def __init__(
        self,
        *,
        testnet: bool = False,
        on_tick: Optional[Callable[[Tick], None]] = None,
        symbols: Optional[list[str]] = None,
    ) -> None:
        self.testnet = testnet
        self.on_tick = on_tick
        self.symbols: list[str] = list(symbols or [])
        self._ws = None
        self._running = False
        self._lock = threading.Lock()
        self.last_tick_ts = 0.0
        self._tick_count = 0
        self._reconnects = 0

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def connect(self) -> None:
        self._open_ws()

    def _open_ws(self) -> None:
        from pybit.unified_trading import WebSocket

        with self._lock:
            self._ws = WebSocket(testnet=self.testnet, channel_type="linear")
        logger.info("public trade feed connected testnet=%s", self.testnet)

    def subscribe(self, symbols: list[str]) -> None:
        self.symbols = list(symbols)
        self._subscribe_locked()
        self._running = True

    def _subscribe_locked(self) -> None:
        if self._ws is None:
            raise RuntimeError("call connect() first")

        def _handler(message: dict) -> None:
            try:
                data = message.get("data")
                if not data:
                    return
                rows = data if isinstance(data, list) else [data]
                for row in rows:
                    symbol = row.get("s") or row.get("symbol")
                    price = float(row.get("p") or row.get("price"))
                    size = float(row.get("v") or row.get("size") or 0)
                    ts = int(row.get("T") or row.get("ts") or time.time() * 1000)
                    aggressor = row.get("S")  # "Buy" / "Sell"
                    tick = Tick(
                        symbol=symbol,
                        price=price,
                        size=size,
                        ts_ms=ts,
                        aggressor=aggressor,
                    )
                    self.last_tick_ts = time.time()
                    self._tick_count += 1
                    if self.on_tick:
                        self.on_tick(tick)
            except Exception:
                logger.exception("public trade handler error")

        for symbol in self.symbols:
            self._ws.trade_stream(symbol=symbol, callback=_handler)
        logger.info("subscribed public trades: %s", self.symbols)

    def ensure_alive(self, *, stale_sec: float = 45.0) -> bool:
        """
        If no ticks for stale_sec while running, tear down and reconnect.
        Returns True if a reconnect was performed.
        """
        if not self._running or not self.symbols:
            return False
        # grace period right after start
        if self.last_tick_ts <= 0:
            return False
        age = time.time() - self.last_tick_ts
        if age < stale_sec:
            return False

        logger.error(
            "WS stale: no ticks for %.1fs — reconnecting (reconnects=%d)",
            age,
            self._reconnects,
        )
        self.reconnect()
        return True

    def reconnect(self) -> None:
        with self._lock:
            try:
                self._ws = None
            except Exception:
                pass
        time.sleep(1.0)
        try:
            self._open_ws()
            self._subscribe_locked()
            self._reconnects += 1
            self.last_tick_ts = time.time()  # avoid immediate re-trigger
            logger.info("WS reconnected (#%d) symbols=%s", self._reconnects, self.symbols)
        except Exception:
            logger.exception("WS reconnect failed")

    def close(self) -> None:
        self._running = False
        with self._lock:
            self._ws = None
