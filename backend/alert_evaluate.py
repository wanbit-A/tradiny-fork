# This software is licensed under a dual-license model:
# 1. Under the Affero General Public License (AGPL) for open-source use.
# 2. With additional terms tailored to individual users (e.g., traders and investors):
#
#    - Individual users may use this software for personal profit (e.g., trading/investing)
#      without releasing proprietary strategies.
#
#    - Redistribution, public tools, or commercial use require compliance with AGPL
#      or a commercial license. Contact: license@tradiny.com
#
# For full details, see the LICENSE.md file in the root directory of this project.

from datetime import datetime, timedelta, timezone
from asyncio import create_task
import logging
import uuid

from db import (
    get_alert_by_id,
    update_alert_next_tick,
    update_alert_notified_at,
    update_expiry_notification,
    update_added_notification,
)

from config import Config
from notification import send_notification

from rules_evaluate import rules_evaluate, get_key

# Lazy CCXT markets cache (alert workers cannot reach the CCXT Provider process).
_ccxt_markets = None
_ccxt_markets_exchange_id = None


def get_tickers(data_provider_config):
    """Unique list of {source, name, interval} the alert is watching."""
    tickers = []
    seen = set()
    for d in (data_provider_config or {}).get("data", []):
        if d.get("type") == "data":
            key = (d.get("source"), d.get("name"), d.get("interval"))
            if key not in seen:
                seen.add(key)
                tickers.append(
                    {
                        "source": d.get("source"),
                        "name": d.get("name"),
                        "interval": d.get("interval"),
                    }
                )
    return tickers


def build_conditions(rules, indicators, data_values):
    """Human-readable per-rule condition breakdown (label, value, comparator)."""
    conditions = []
    for rule in rules:
        k1, l1 = get_key(indicators, "1", rule)
        k2, l2 = get_key(indicators, "2", rule)

        conditions.append(
            {
                "left": l1 if l1 else "value",
                "left_value": data_values.get(l1) if l1 else rule.get("value1"),
                "comparator": rule.get("comparator"),
                "right": l2 if l2 else "value",
                "right_value": data_values.get(l2) if l2 else rule.get("value2"),
            }
        )
    return conditions

def _get_ccxt_markets():
    """Load/cached markets for symbol validation + precision. Isolated from Provider process."""
    global _ccxt_markets, _ccxt_markets_exchange_id
    exchange_id = (getattr(Config, "CCXT_EXCHANGE_ID", None) or "binance").strip().lower()
    if _ccxt_markets is not None and _ccxt_markets_exchange_id == exchange_id:
        return _ccxt_markets
    try:
        import ccxt
        if not hasattr(ccxt, exchange_id):
            logging.warning(f"CCXT has no exchange {exchange_id!r}")
            return None
        exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        _ccxt_markets = exchange.load_markets()
        _ccxt_markets_exchange_id = exchange_id
        return _ccxt_markets
    except Exception as e:
        logging.warning(f"Failed to load CCXT markets for webhook context: {e}")
        return None


def _precision_to_decimals(value):
    """CCXT precision is either decimal-places (int) or a tick size (float)."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        f = float(value)
        if f >= 1:
            return 0
        s = f"{f:.16f}".rstrip("0")
        if "." in s:
            return len(s.split(".")[1])
        return 0
    except (TypeError, ValueError):
        return None


def _enrich_from_ticker(context, tickers, lastDataPoint):
    """Add signalId, price, exchangeSymbol, interval, precision. Returns False if symbol invalid."""
    context["signalId"] = str(uuid.uuid4())

    if not tickers:
        return True

    t0 = tickers[0]
    ticker = t0.get("name")
    interval = t0.get("interval")
    source = t0.get("source") or "CCXT"

    context["ticker"] = ticker
    if interval:
        context["interval"] = interval

    # Price from last closed/live candle key: CCXT-BTC/USDT-5m-close
    if lastDataPoint and ticker and interval:
        close_key = f"{source}-{ticker}-{interval}-close"
        price = lastDataPoint.get(close_key)
        if price is not None:
            try:
                context["price"] = float(price)
            except (TypeError, ValueError):
                pass

    # Exchange-native symbol + precision from CCXT markets
    markets = _get_ccxt_markets()
    if markets is not None and ticker:
        market = markets.get(ticker)
        if market is None:
            logging.error(
                f"Webhook blocked: symbol {ticker!r} not in CCXT markets "
                f"(exchange={getattr(Config, 'CCXT_EXCHANGE_ID', None)})"
            )
            return False
        context["exchangeSymbol"] = market.get("id") or ticker.replace("/", "")
        # Prefer raw exchange info (MEXC) so names match exchangeInfo semantics.
        info = market.get("info") or {}
        prec = market.get("precision") or {}

        # Decimal places (MEXC: baseAssetPrecision / quotePrecision)
        if info.get("baseAssetPrecision") is not None:
            try:
                context["baseAssetPrecision"] = int(info["baseAssetPrecision"])
            except (TypeError, ValueError):
                context["baseAssetPrecision"] = _precision_to_decimals(prec.get("amount"))
        else:
            context["baseAssetPrecision"] = _precision_to_decimals(prec.get("amount"))

        if info.get("quotePrecision") is not None:
            try:
                context["quotePrecision"] = int(info["quotePrecision"])
            except (TypeError, ValueError):
                context["quotePrecision"] = _precision_to_decimals(prec.get("price"))
        else:
            context["quotePrecision"] = _precision_to_decimals(prec.get("price"))

        # Lot step (MEXC: baseSizePrecision is a STRING step, e.g. "0.01")
        step = info.get("baseSizePrecision")
        if step is not None and str(step).strip() != "":
            context["baseSizePrecision"] = str(step)  # keep as string, same as exchangeInfo
        else:
            # Fallback: derive step from CCXT amount precision if info missing
            dec = _precision_to_decimals(prec.get("amount"))
            if dec is not None:
                context["baseSizePrecision"] = str(10 ** (-dec) if dec > 0 else 1)
    elif ticker:
        # Soft fallback if markets unavailable — do not block the alert
        context["exchangeSymbol"] = ticker.replace("/", "")

    return True

def build_context(alert, event, data_values=None, lastDataPoint=None):
    """
    Build webhook payload context.
    Returns None if the symbol is invalid (caller should skip the webhook POST).
    """
    settings = alert["settings"]
    tickers = get_tickers(settings.get("dataProviderConfig"))
    context = {
        "event": event,  # "added" | "matched" | "expired"
        "alert_id": alert["id"],
        "tickers": tickers,
        "exchange": getattr(Config, "CCXT_EXCHANGE_ID", None),
    }

    signal_type = settings.get("signal_type")
    if signal_type:
        context["signalType"] = signal_type

    if data_values is not None:
        context["conditions"] = build_conditions(
            settings.get("rules", []), settings.get("indicators", {}), data_values
        )
        context["values"] = data_values

    if lastDataPoint:
        context["last_data_point"] = lastDataPoint

    if not _enrich_from_ticker(context, tickers, lastDataPoint):
        return None

    return context


def alert_evaluate(dbconn, message, alert, data):

    alert_id = alert["id"]
    alert = get_alert_by_id(dbconn, alert_id)
    if alert is None:
        logging.info(f"Alert no longer exists, closing subscription for alert_id={alert_id}")
        create_task(data["websocket_client"].close())
        data["in_progress"] = False
        return

    alert_message = alert["settings"]["message"]
    now = datetime.now(timezone.utc)

    if (
        alert["expire_date"] is not None
        and alert["expiry_notification_sent_at"] is None
        and alert["expire_date"] < now
    ):
        update_expiry_notification(dbconn, alert["id"], now)
        send_notification(
            alert["settings"]["subscription"],
            f"Alert expired: {alert_message}",
            alert["settings"].get("webhook_url"),
            build_context(alert, "expired"),
        )

    if alert["expire_date"] is not None and alert["expire_date"] < now:
        create_task(data["websocket_client"].close())
        data["in_progress"] = False
        return

    lastDataPoint = data["lastDataPoint"] if "lastDataPoint" in data else {}

    if alert["added_notification_sent_at"] is None and not lastDataPoint:
        update_added_notification(dbconn, alert["id"], now)
        send_notification(
            alert["settings"]["subscription"],
            f"Alert added: {alert_message}",
            alert["settings"].get("webhook_url"),
            build_context(alert, "added"),
        )

    # Default: evaluate only on confirmed candle close.
    # Set alert settings "evaluate_on": "intrabar" to allow live ticks.
    evaluate_on = (alert["settings"].get("evaluate_on") or "close").lower()

    should_evaluate = False

    if message["type"] == "data_init":
        lastDataPoint.update(message["data"][-1])
        # Initial snapshot is not a close event.
        should_evaluate = evaluate_on == "intrabar"

    elif message["type"] == "indicator_init":
        lastDataPoint.update(message["data"][-1])
        should_evaluate = evaluate_on == "intrabar"

    elif message["type"] == "data_update":
        # Live / forming candle.
        if evaluate_on == "intrabar":
            # Intrabar mode: everything is live, close included.
            lastDataPoint.update(message["data"])
        else:
            # Close mode: stream O/H/L/V live, but keep every "-close" key
            # pinned to the last confirmed close. "close" must always mean
            # the CLOSED candle value; the other keys are live estimates.
            for k, v in message["data"].items():
                if not k.endswith("-close"):
                    lastDataPoint[k] = v
        should_evaluate = evaluate_on == "intrabar"

    elif message["type"] == "candle_close":
        # Confirmed / finalized candle.
        lastDataPoint.update(message["data"])
        logging.info(
            f"Alert {alert['id']}: received CLOSED candle "
            f"from {message.get('source')} "
            f"{message.get('name')} "
            f"{message.get('interval')}"
        )
        # Close-mode and intrabar-mode both evaluate on close.
        should_evaluate = True

    elif message["type"] == "indicator_update":
        lastDataPoint.update(message["data"])
        # The backend never sets "closed" on indicator payloads, so this
        # event only evaluates in intrabar mode. In close mode, indicator
        # rules fire on the candle_close event.
        should_evaluate = evaluate_on == "intrabar"

    data["lastDataPoint"] = lastDataPoint

    if not should_evaluate:
        return

    rules = alert["settings"]["rules"]
    operators = alert["settings"]["operators"]
    indicators = alert["settings"]["indicators"]

    rules_result = rules_evaluate(rules, operators, indicators, lastDataPoint)
    if rules_result is None:
        logging.debug(
            f"Alert {alert['id']}: rules_evaluate returned None "
            f"(data not ready yet)"
        )
        return
    result, data_values = rules_result

    if result and alert["next_tick"] == 1:
        logging.info(f"alert {alert['id']} matched")
        update_alert_next_tick(dbconn, alert["id"], 0)

        ctx = build_context(alert, "matched", data_values, lastDataPoint)
        if ctx is None:
            logging.error(
                f"alert {alert['id']} matched but webhook skipped (invalid symbol)"
            )
        else:
            send_notification(
                alert["settings"]["subscription"],
                alert_message,
                alert["settings"].get("webhook_url"),
                ctx,
            )

    if not result and alert["next_tick"] == 0:
        update_alert_next_tick(dbconn, alert["id"], 1)