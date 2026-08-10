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

import logging

from config import Config


def send_notification(message):
    """Always-on notification channel that logs to the console/log file.

    Web push requires a browser subscription and email requires SMTP
    credentials, so neither is guaranteed to work out of the box in a local
    dev environment. This channel has no external dependencies, so it's a
    reliable way to confirm the notification pipeline is firing while
    developing locally. It can be disabled by setting DISABLE_CONSOLE_NOTIFICATIONS=1.
    """
    if getattr(Config, "DISABLE_CONSOLE_NOTIFICATIONS", "0") == "1":
        return

    logging.info(f"[NOTIFICATION] {message}")