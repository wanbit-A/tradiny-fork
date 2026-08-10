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

import json
import logging
import time
import urllib.request
import urllib.error

from config import Config

TIMEOUT_SECONDS = 5
MAX_RETRIES = 2


def send_notification(webhook_url, message, context=None):
    """POST a JSON payload to a user-supplied webhook URL.

    webhook_url is per-alert (set from the frontend) and falls back to the
    global WEBHOOK_URL config value if not provided. `context` is an optional
    dict with extra trading-relevant fields (tickers, exchange, conditions,
    values, last_data_point) merged into the payload so tools like n8n have
    enough data to act on the alert automatically.
    """
    url = webhook_url or getattr(Config, "WEBHOOK_URL", "")
    if not url:
        return

    body = {"message": message, "sent_at": time.time()}
    if context:
        body.update(context)

    payload = json.dumps(body, default=str).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                logging.info(
                    f"Webhook notification sent to {url} (status {resp.status})."
                )
                return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            logging.warning(f"Webhook attempt {attempt}/{MAX_RETRIES} failed: {e}")
            time.sleep(0.5)

    logging.error(f"Unable to send webhook notification to {url}: {last_error}")