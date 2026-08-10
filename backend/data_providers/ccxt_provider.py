import logging
import re
import ccxt
from datetime import datetime, timedelta, timezone
import threading
import json
import time

from provider import Provider
from config import Config


# Musk Step 5: assert the class shape at import time so a "complete file"
# rewrite that drops a method fails loudly here, not silently at first request.
_REQUIRED_METHODS = (
    "init",
    "_init_exchange",
    "get_dataset",
    "format_datapoint",
    "get_history",
    "start_streaming",
    "_start_ccxt_stream",
    "_push_datapoint",
    "no_update",
    "on_close",
)


class CCXTProvider(Provider):
    key = "CCXT"
    type = "candlestick"

    lock = threading.Lock()
    ws_clients = {}
    streams = {}
    streams_started_at = {}
    streams_stopped_at = {}

    INTERVAL_MAP = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
        "1d": "1d", "1w": "1w", "1M": "1M",
    }
    SUPPORTED_INTERVALS = list(INTERVAL_MAP.keys())

    # Refresh cadence for the polling fallback (in seconds).
    # Independent of candle interval: a 1d chart still refreshes every
    # POLL_REFRESH_SEC so the in-progress candle is visible, instead of
    # waiting 86400s for the day to close. Tune for your exchange's
    # rate-limit budget (Binance public = 1200 req/min, 10s is fine).
    # For MEXC - 1 sec is fine with 10 concurrent symbols
    POLL_REFRESH_SEC = 1

    # ------------------------------------------------------------------
    # Defensive coercion — turns whatever the framework hands us into a
    # clean int ms (or None) for CCXT's `since` parameter.
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_since(start_time_query):
        """
        Returns int ms, or None.
        - None / "" / "null" / 0          -> None
        - int or float in seconds         -> * 1000
        - int or float in ms              -> as-is
        - numeric string                  -> parsed + heuristic
        - datetime / datetime string      -> parsed to ms
        - anything else                   -> None (don't poison the request)
        """
        if start_time_query is None:
            return None

        if isinstance(start_time_query, datetime):
            dt = (start_time_query
                  if start_time_query.tzinfo is not None
                  else start_time_query.replace(tzinfo=timezone.utc))
            return int(dt.timestamp() * 1000)

        if isinstance(start_time_query, (int, float)):
            v = int(start_time_query)
            if v == 0:
                return None
            # < 1e12 looks like seconds; >= 1e12 looks like ms
            return v * 1000 if v < 10**12 else v

        if isinstance(start_time_query, str):
            s = start_time_query.strip()
            if not s or s.lower() in ("none", "null", "0"):
                return None
            if s.isdigit():
                v = int(s)
                if v == 0:
                    return None
                return v * 1000 if v < 10**12 else v
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d",
            ):
                try:
                    dt = datetime.strptime(s, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return int(dt.timestamp() * 1000)
                except ValueError:
                    continue

        return None  # last resort: don't pass garbage to CCXT

    def init(self):
        # Loud banner so any init-time crash is immediately attributable.
        logging.info(f"[CCXT] init starting (exchange={getattr(Config, 'CCXT_EXCHANGE_ID', 'mexc')})")
        self._init_exchange()
        # Load markets once at boot so the first fetch_ohlcv doesn't pay
        # the cold-start cost (and so symbol validation works).
        try:
            self.exchange.load_markets()
        except Exception as e:
            logging.warning(f"[CCXT] load_markets at boot failed: {e}")
        logging.info(f"[CCXT] init complete (exchange={self.exchange.id})")

    def _init_exchange(self):
        exchange_id = (getattr(Config, "CCXT_EXCHANGE_ID", None) or "binance").strip().lower()
        if not exchange_id:
            exchange_id = "binance"

        api_key = (getattr(Config, "CCXT_API_KEY", None) or "").strip()
        api_secret = (getattr(Config, "CCXT_API_SECRET", None) or "").strip()

        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"CCXT does not support exchange: {exchange_id!r}")

        params = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
                "recvWindow": 60000,
            },
        }
        # Only attach keys if both are real — empty strings still make CCXT sign
        if api_key and api_secret:
            params["apiKey"] = api_key
            params["secret"] = api_secret

        exchange_cls = getattr(ccxt, exchange_id)
        self.exchange = exchange_cls(params)
        logging.info(f"CCXTProvider initialized with exchange: {exchange_id}")
    def get_dataset(self):
        if not hasattr(self, "exchange") or self.exchange is None:
            self._init_exchange()
        try:
            markets = self.exchange.load_markets()
        except Exception as e:
            logging.error(f"CCXT failed to load markets: {e}")
            return []

        symbols = []
        for symbol, market in markets.items():
            if not market.get("active", False):
                continue
            if market.get("type") != "spot" and market.get("spot") is not True:
                continue

            base = market.get("base", "")
            quote = market.get("quote", "")
            name_label = f"{base}/{quote}" if base and quote else symbol

            symbols.append({
                "source": self.key,
                "name": symbol,
                "name_label": name_label,
                "type": self.type,
                "categories": ["Crypto"],
                "intervals": self.SUPPORTED_INTERVALS,
                "outputs": [
                    {"name": "open",   "y_axis": "price"},
                    {"name": "high",   "y_axis": "price"},
                    {"name": "low",    "y_axis": "price"},
                    {"name": "close",  "y_axis": "price"},
                    {"name": "volume", "y_axis": "volume"},
                ],
            })

        logging.info(f"CCXTProvider loaded {len(symbols)} symbols from {self.exchange.id}")
        return symbols

    def format_datapoint(self, symbol, interval, k):
        ts_ms = k[0]
        date_str = datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return {
            "date": date_str,
            f"{self.key}-{symbol}-{interval}-open":   k[1],
            f"{self.key}-{symbol}-{interval}-high":   k[2],
            f"{self.key}-{symbol}-{interval}-low":    k[3],
            f"{self.key}-{symbol}-{interval}-close":  k[4],
            f"{self.key}-{symbol}-{interval}-volume": k[5],
        }

    def get_history(self, symbol, interval, start_time_query, end_time_query, count):
        # Musk Step 2/3: inlined. _to_ccxt_timeframe was a one-line wrapper
        # around a dict lookup — easy to lose in a rewrite, easier to inline.
        timeframe = self.INTERVAL_MAP.get(interval, interval)
        since = self._normalize_since(start_time_query)

        logging.info(
            f"[CCXT] get_history symbol={symbol!r} interval={interval!r} "
            f"since={since!r} count={count!r}"
        )

        try:
            ohlcv = self.exchange.fetch_ohlcv(
                symbol,
                timeframe,
                since=since,
                limit=count,
            )
        except ccxt.BadSymbol as e:
            logging.error(f"[CCXT] bad symbol {symbol!r}: {e}")
            return []
        except ccxt.NetworkError as e:
            logging.error(f"[CCXT] network error fetch_ohlcv({symbol}, {interval}): {e}")
            return []
        except ccxt.ExchangeError as e:
            logging.error(f"[CCXT] exchange error fetch_ohlcv({symbol}, {interval}): {e}")
            return []
        except Exception as e:
            logging.error(f"CCXT fetch_ohlcv error for {symbol} {interval}: {e}")
            return []

        logging.info(f"[CCXT] fetch_ohlcv returned {len(ohlcv) if ohlcv else 0} rows")

        if not ohlcv:
            logging.warning(
                f"[CCXT] fetch_ohlcv returned 0 rows for {symbol} {interval} "
                f"(since={since}, limit={count})"
            )
            return []

        return [self.format_datapoint(symbol, interval, row) for row in ohlcv]

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    def start_streaming(self, ws_client, symbol, interval):
        if (symbol, interval) in CCXTProvider.streams_stopped_at:
            diff = datetime.now(timezone.utc) - CCXTProvider.streams_stopped_at[(symbol, interval)]
            seconds = 5
            if diff <= timedelta(seconds=seconds):
                if (symbol, interval) not in CCXTProvider.streams_stopped_at:
                    CCXTProvider.streams_stopped_at[(symbol, interval)] = datetime.now(timezone.utc)
                    return self._schedule_message(
                        seconds - diff.total_seconds(),
                        {"action": "start_streaming", "args": (ws_client, symbol, interval)},
                    )
                else:
                    return

        if (symbol, interval) in CCXTProvider.streams_stopped_at:
            del CCXTProvider.streams_stopped_at[(symbol, interval)]

        with CCXTProvider.lock:
            if (symbol, interval) not in CCXTProvider.ws_clients:
                CCXTProvider.ws_clients[(symbol, interval)] = []

            if ws_client is not None:
                CCXTProvider.ws_clients[(symbol, interval)].append(ws_client)
                self._start_ccxt_stream(symbol, interval)

    def _start_ccxt_stream(self, symbol, interval):
        if (symbol, interval) in CCXTProvider.streams:
            return

        timeframe = self.INTERVAL_MAP.get(interval, interval)
        exchange = self.exchange

        def stream_loop():
            logging.info(f"Starting CCXT stream for {symbol} {interval}")
            CCXTProvider.streams_started_at[(symbol, interval)] = datetime.now(timezone.utc)

            # Try CCXT Pro first. hasattr is unreliable (binance advertises
            # watch_ohlcv but raises NotSupported at runtime), so the
            # only honest check is "try once, catch NotSupported".
            try:
                if hasattr(exchange, "watch_ohlcv"):
                    self._stream_with_ccxt_pro(symbol, interval, timeframe)
                    return
            except ccxt.NotSupported as e:
                logging.info(
                    f"[CCXT] {exchange.id} doesn't support watch_ohlcv ({e}); "
                    f"falling back to polling for {symbol} {interval}."
                )
            except Exception as e:
                logging.warning(
                    f"[CCXT] watch_ohlcv probe failed for {exchange.id} "
                    f"{symbol} {interval}: {e}; falling back to polling."
                )

            self._stream_with_polling(symbol, interval, timeframe)

        thread = threading.Thread(target=stream_loop, daemon=True)
        CCXTProvider.streams[(symbol, interval)] = thread
        thread.start()

    def _stream_with_ccxt_pro(self, symbol, interval, timeframe):
        """Stream using CCXT Pro's watch_ohlcv.

        Raises ccxt.NotSupported so the caller can fall back to polling.
        Other exceptions are logged and retried.
        """
        import asyncio

        async def watch():
            while (symbol, interval) in CCXTProvider.streams:
                try:
                    ohlcv = await self.exchange.watch_ohlcv(symbol, timeframe)
                    if ohlcv:
                        # CCXT may return a partial or full candle list;
                        # the last entry is the latest tick.
                        for row in ohlcv[-1:]:
                            self._push_datapoint(symbol, interval, row)
                except ccxt.NotSupported:
                    # Bubble up so the caller switches to polling.
                    raise
                except Exception as e:
                    logging.error(
                        f"CCXT Pro watch error for {symbol} {interval}: {e}"
                    )
                    await asyncio.sleep(5)

        asyncio.run(watch())

    def _stream_with_polling(self, symbol, interval, timeframe):
        last_ts = 0
        last_ohlcv = None
        refresh = self.POLL_REFRESH_SEC

        while (symbol, interval) in CCXTProvider.streams:
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=2)
                if ohlcv:
                    latest = ohlcv[-1]
                    ts = latest[0]
                    values = (latest[1], latest[2], latest[3], latest[4], latest[5])
                    if ts > last_ts or values != last_ohlcv:
                        last_ts = ts
                        last_ohlcv = values
                        logging.info(
                            f"[CCXT] push {symbol} {interval} close={values[3]}"
                        )  # temporary — prove pushes happen
                        self._push_datapoint(symbol, interval, latest)
            except Exception as e:
                logging.error(f"CCXT poll error for {symbol} {interval}: {e}")

            time.sleep(refresh)
    def _push_datapoint(self, symbol, interval, ohlcv_row):
        data = self.format_datapoint(symbol, interval, ohlcv_row)

        self.respond({
            "action": "update_in_cache",
            "args": (self.key, symbol, interval, [data]),
        })

        ws_clients = CCXTProvider.ws_clients.get((symbol, interval), [])
        if len(ws_clients) > 0:
            self.respond({
                "action": "write_message",
                "ws_clients": ws_clients,
                "source": self.key,
                "name": symbol,
                "interval": interval,
                "args": [json.dumps({
                    "type": "data_update",
                    "source": self.key,
                    "name": symbol,
                    "interval": interval,
                    "data": data,
                })],
            })

    def no_update(self, symbol, interval):
        logging.info(f"No update for {symbol} {interval}, restarting CCXT stream...")
        with CCXTProvider.lock:
            if (symbol, interval) in CCXTProvider.streams:
                del CCXTProvider.streams[(symbol, interval)]
                CCXTProvider.streams_stopped_at[(symbol, interval)] = datetime.now(timezone.utc)
        self.start_streaming(None, symbol, interval)

    def on_close(self, ws_client, symbol, interval):
        with CCXTProvider.lock:
            if (symbol, interval) in CCXTProvider.ws_clients:
                if ws_client in CCXTProvider.ws_clients[(symbol, interval)]:
                    CCXTProvider.ws_clients[(symbol, interval)].remove(ws_client)
                remaining = len(CCXTProvider.ws_clients[(symbol, interval)])
                logging.info(f"{symbol} {interval} has {remaining} clients remaining.")
                if remaining == 0:
                    del CCXTProvider.ws_clients[(symbol, interval)]
                    if (symbol, interval) in CCXTProvider.streams:
                        logging.info(f"{symbol} {interval} stopping stream.")
                        del CCXTProvider.streams[(symbol, interval)]
                        CCXTProvider.streams_stopped_at[(symbol, interval)] = datetime.now(timezone.utc)

    def _schedule_message(self, delay, message):
        def delayed_execution():
            time.sleep(delay)
            self.respond(message)
        thread = threading.Thread(target=delayed_execution)
        thread.start()


# Musk Step 5 (continued): run the structural check after the class body
# is defined. If anyone (me, you, future-you) drops a method in a "complete"
# rewrite, this fails at import — before the worker process even starts —
# instead of at first request.
_missing = [m for m in _REQUIRED_METHODS if not hasattr(CCXTProvider, m)]
assert not _missing, (
    f"CCXTProvider is missing required method(s): {_missing}. "
    f"A patch or rewrite dropped them — fix the class definition before booting."
)
