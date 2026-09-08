import logging
import ccxt
import ccxt.pro as ccxtpro
from datetime import datetime, timedelta, timezone
import threading
import json
import time
import asyncio

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

        # Live websocket client (CCXT Pro). History stays on REST self.exchange.
        ws_cls = getattr(ccxtpro, exchange_id, None)
        if ws_cls is None:
            self.ws_exchange = None
            logging.warning(
                f"[CCXT] no ccxt.pro class for {exchange_id!r}; "
                f"websocket streaming disabled"
            )
        else:
            ws_params = {
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                    "recvWindow": 60000,
                },
            }
            if api_key and api_secret:
                ws_params["apiKey"] = api_key
                ws_params["secret"] = api_secret
            self.ws_exchange = ws_cls(ws_params)
            logging.info(f"[CCXT] Pro WS client ready for {exchange_id}")
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

        date_str = datetime.fromtimestamp(
            ts_ms / 1000,
            timezone.utc,
        ).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "date": date_str,
            "timestamp": ts_ms,

            f"{self.key}-{symbol}-{interval}-open": k[1],
            f"{self.key}-{symbol}-{interval}-high": k[2],
            f"{self.key}-{symbol}-{interval}-low": k[3],
            f"{self.key}-{symbol}-{interval}-close": k[4],
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
            logging.info(
                f"[CCXT] Starting stream for {symbol} {interval} "
                f"using exchange={exchange.id}"
            )
            CCXTProvider.streams_started_at[(symbol, interval)] = (
                datetime.now(timezone.utc)
            )

            used_ws = False
            if getattr(self, "ws_exchange", None) is not None:
                try:
                    logging.info(
                        f"[CCXT] Trying CCXT Pro watch_ohlcv for "
                        f"{symbol} {interval}"
                    )
                    self._stream_with_ccxt_pro(symbol, interval, timeframe)
                    used_ws = True
                except ccxt.NotSupported as e:
                    logging.info(
                        f"[CCXT] watch_ohlcv not supported ({e}); "
                        f"falling back to REST polling"
                    )
                except Exception as e:
                    logging.warning(
                        f"[CCXT] watch_ohlcv failed "
                        f"({type(e).__name__}: {e}); "
                        f"falling back to REST polling"
                    )

            if not used_ws and (symbol, interval) in CCXTProvider.streams:
                logging.info(
                    f"[CCXT] Starting REST polling for "
                    f"{symbol} {interval} "
                    f"refresh={self.POLL_REFRESH_SEC}s"
                )
                self._stream_with_polling(symbol, interval, timeframe)

        thread = threading.Thread(target=stream_loop, daemon=True)
        CCXTProvider.streams[(symbol, interval)] = thread
        thread.start()

    def _stream_with_ccxt_pro(self, symbol, interval, timeframe):
        """
        Live stream via CCXT Pro watch_ohlcv.

        Important: the Pro exchange instance must be created inside the
        asyncio loop that will use it. A shared self.ws_exchange created
        at init lives on a different loop and causes:
          RuntimeError: Future attached to a different loop
        """
        exchange_id = self.exchange.id

        async def watch():
            # Fresh client bound to THIS loop (this thread).
            ws = getattr(ccxtpro, exchange_id)({
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                    "recvWindow": 60000,
                },
            })

            last_live_ts = None
            last_live_values = None
            last_closed_ts = None
            prev_row = None
            consecutive_errors = 0

            logging.info(
                f"[CCXT] WS LOOP ENTERED {symbol} {interval} "
                f"timeframe={timeframe}"
            )

            try:
                while (symbol, interval) in CCXTProvider.streams:
                    try:
                        ohlcv = await ws.watch_ohlcv(symbol, timeframe)
                        consecutive_errors = 0

                        if not ohlcv:
                            continue

                        row = ohlcv[-1]
                        ts = row[0]
                        values = (row[1], row[2], row[3], row[4], row[5])

                        # CLOSED when candle timestamp advances
                        if last_live_ts is not None and ts > last_live_ts:
                            closed_row = prev_row
                            if closed_row is not None:
                                closed_ts = closed_row[0]
                                if (
                                    last_closed_ts is None
                                    or closed_ts > last_closed_ts
                                ):
                                    last_closed_ts = closed_ts
                                    logging.info(
                                        f"[CCXT] CLOSED {symbol} {interval} "
                                        f"ts={closed_ts} "
                                        f"O={closed_row[1]} "
                                        f"H={closed_row[2]} "
                                        f"L={closed_row[3]} "
                                        f"C={closed_row[4]} "
                                        f"V={closed_row[5]}"
                                    )
                                    self._push_datapoint(
                                        symbol,
                                        interval,
                                        closed_row,
                                        event_type="candle_close",
                                        closed=True,
                                    )

                        # LIVE forming candle
                        if (
                            ts != last_live_ts
                            or values != last_live_values
                        ):
                            last_live_ts = ts
                            last_live_values = values
                            prev_row = row

                            logging.debug(
                                f"[CCXT] LIVE {symbol} {interval} "
                                f"ts={ts} "
                                f"O={row[1]} H={row[2]} L={row[3]} "
                                f"C={row[4]} V={row[5]}"
                            )
                            self._push_datapoint(
                                symbol,
                                interval,
                                row,
                                event_type="data_update",
                                closed=False,
                            )

                    except ccxt.NotSupported:
                        raise
                    except Exception as e:
                        consecutive_errors += 1
                        logging.exception(
                            f"[CCXT] WS error {symbol} {interval}: {e}"
                        )
                        # After a few loop-binding / fatal failures, bail out
                        # so stream_loop can fall back to REST polling.
                        if consecutive_errors >= 3:
                            raise
                        await asyncio.sleep(2)

            finally:
                try:
                    await ws.close()
                except Exception:
                    pass

        asyncio.run(watch())

    def _stream_with_polling(self, symbol, interval, timeframe):
        """
        REST polling fallback (Phase 1.5 semantics).
        """
        last_live_ts = None
        last_live_values = None
        last_closed_ts = None

        refresh = self.POLL_REFRESH_SEC
        tf_ms = self.exchange.parse_timeframe(timeframe) * 1000

        logging.info(
            f"[CCXT] POLLING LOOP ENTERED "
            f"{symbol} {interval} timeframe={timeframe}"
        )

        while (symbol, interval) in CCXTProvider.streams:
            try:
                logging.debug(
                    f"[CCXT] polling fetch START {symbol} {interval}"
                )

                fetch_started = time.time()
                ohlcv = self.exchange.fetch_ohlcv(
                    symbol,
                    timeframe,
                    limit=5,
                )
                fetch_elapsed = time.time() - fetch_started

                logging.debug(
                    f"[CCXT] polling fetch DONE {symbol} {interval} "
                    f"rows={len(ohlcv) if ohlcv else 0} "
                    f"elapsed={fetch_elapsed:.3f}s"
                )

                if not ohlcv or len(ohlcv) < 2:
                    logging.warning(
                        f"[CCXT] polling returned insufficient OHLCV "
                        f"for {symbol} {interval}: "
                        f"{len(ohlcv) if ohlcv else 0} rows"
                    )
                    time.sleep(refresh)
                    continue

                live_candle = ohlcv[-1]
                live_ts = live_candle[0]
                finished = ohlcv[:-1]

                if last_closed_ts is None:
                    last_closed_ts = finished[-1][0]
                    logging.info(
                        f"[CCXT] polling initialized "
                        f"{symbol} {interval} "
                        f"closed_ts={last_closed_ts}"
                    )
                else:
                    for row in finished:
                        row_ts = row[0]
                        if row_ts <= last_closed_ts:
                            continue

                        last_closed_ts = row_ts
                        logging.info(
                            f"[CCXT] CLOSED {symbol} {interval} "
                            f"ts={row_ts} "
                            f"O={row[1]} H={row[2]} L={row[3]} "
                            f"C={row[4]} V={row[5]}"
                        )
                        self._push_datapoint(
                            symbol,
                            interval,
                            row,
                            event_type="candle_close",
                            closed=True,
                        )

                live_values = (
                    live_candle[1],
                    live_candle[2],
                    live_candle[3],
                    live_candle[4],
                    live_candle[5],
                )

                if (
                    live_ts != last_live_ts
                    or live_values != last_live_values
                ):
                    last_live_ts = live_ts
                    last_live_values = live_values

                    logging.info(
                        f"[CCXT] LIVE {symbol} {interval} "
                        f"ts={live_ts} "
                        f"O={live_candle[1]} "
                        f"H={live_candle[2]} "
                        f"L={live_candle[3]} "
                        f"C={live_candle[4]} "
                        f"V={live_candle[5]}"
                    )
                    self._push_datapoint(
                        symbol,
                        interval,
                        live_candle,
                        event_type="data_update",
                        closed=False,
                    )

                now_ms = self.exchange.milliseconds()
                close_at = live_ts + tf_ms
                ms_to_close = close_at - now_ms

                if 0 < ms_to_close <= 3000:
                    sleep_s = 0.5
                elif ms_to_close > 3000:
                    sleep_s = min(
                        refresh,
                        max(0.5, (ms_to_close / 1000.0) - 1.0),
                    )
                else:
                    sleep_s = 0.5

                time.sleep(sleep_s)

            except Exception as e:
                logging.exception(
                    f"[CCXT] polling ERROR {symbol} {interval}: {e}"
                )
                time.sleep(refresh)

    def _push_datapoint(self,symbol,interval,ohlcv_row,event_type="data_update",closed=False,):
        data = self.format_datapoint(
            symbol,
            interval,
            ohlcv_row,
        )

        self.respond({
            "action": "update_in_cache",
            "args": (
                self.key,
                symbol,
                interval,
                [data],
            ),
        })

        ws_clients = CCXTProvider.ws_clients.get(
            (symbol, interval),
            [],
        )

        if len(ws_clients) > 0:
            self.respond({
                "action": "write_message",
                "ws_clients": ws_clients,
                "source": self.key,
                "name": symbol,
                "interval": interval,
                "args": [json.dumps({
                    "type": event_type,
                    "source": self.key,
                    "name": symbol,
                    "interval": interval,
                    "closed": closed,
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
