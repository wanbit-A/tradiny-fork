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


def build_context(alert, event, data_values=None, lastDataPoint=None):
    settings = alert["settings"]
    context = {
        "event": event,  # "added" | "matched" | "expired"
        "alert_id": alert["id"],
        "tickers": get_tickers(settings.get("dataProviderConfig")),
        "exchange": getattr(Config, "CCXT_EXCHANGE_ID", None),
    }

    if data_values is not None:
        context["conditions"] = build_conditions(
            settings.get("rules", []), settings.get("indicators", {}), data_values
        )
        context["values"] = data_values

    if lastDataPoint:
        context["last_data_point"] = lastDataPoint

    return context


def alert_evaluate(dbconn, message, alert, data):

    alert = get_alert_by_id(dbconn, alert["id"])
    alert_message = alert["settings"]["message"]
    now = datetime.now(timezone.utc)

    if alert["expiry_notification_sent_at"] is None and alert["expire_date"] < now:
        update_expiry_notification(dbconn, alert["id"], now)
        send_notification(
            alert["settings"]["subscription"],
            f"Alert expired: {alert_message}",
            alert["settings"].get("webhook_url"),
            build_context(alert, "expired"),
        )

    if alert["expire_date"] < now:
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

    if message["type"] == "data_init":
        lastDataPoint.update(message["data"][-1])
    elif message["type"] == "indicator_init":
        lastDataPoint.update(message["data"][-1])
    elif message["type"] == "data_update":
        lastDataPoint.update(message["data"])
    elif message["type"] == "indicator_update":
        lastDataPoint.update(message["data"])
    data["lastDataPoint"] = lastDataPoint

    rules = alert["settings"]["rules"]
    operators = alert["settings"]["operators"]
    indicators = alert["settings"]["indicators"]

    result, data_values = rules_evaluate(rules, operators, indicators, lastDataPoint)

    if result and alert["next_tick"] == 1:
        logging.info(f"alert {alert['id']} matched")
        update_alert_next_tick(dbconn, alert["id"], 0)

        send_notification(
            alert["settings"]["subscription"],
            alert_message,
            alert["settings"].get("webhook_url"),
            build_context(alert, "matched", data_values, lastDataPoint),
        )

    if not result and alert["next_tick"] == 0:
        update_alert_next_tick(dbconn, alert["id"], 1)