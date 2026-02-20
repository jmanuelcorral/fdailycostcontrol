"""
modules — Business logic package for fdailyCostControl.

Contains:
    - cost_client: Azure Cost Management API queries
    - webhook_notifier: HTTP alert delivery to Pandora FMS
    - alert_tracker: Cooldown tracking via Azure Table Storage
"""

from modules.cost_client import get_daily_cost
from modules.webhook_notifier import send_alert, build_alert_data
from modules.alert_tracker import should_send_alert, record_alert_sent

__all__ = [
    "get_daily_cost",
    "send_alert",
    "build_alert_data",
    "should_send_alert",
    "record_alert_sent",
]
