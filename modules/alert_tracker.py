"""
Module to track alert cooldowns using Azure Table Storage.
Prevents sending duplicate alerts for the same Resource Group
within a configurable cooldown period.
"""

import logging
import datetime
import os
from azure.data.tables import TableServiceClient, TableClient

logger = logging.getLogger(__name__)

TABLE_NAME = "CostAlertTracker"


def _get_table_client() -> TableClient:
    """
    Get or create the Azure Table Storage client for alert tracking.
    Uses the AzureWebJobsStorage connection string.
    """
    connection_string = os.environ.get("AzureWebJobsStorage", "UseDevelopmentStorage=true")
    service_client = TableServiceClient.from_connection_string(connection_string)

    # Create table if it doesn't exist
    try:
        service_client.create_table_if_not_exists(TABLE_NAME)
    except Exception as e:
        logger.warning("Could not create table '%s': %s", TABLE_NAME, str(e))

    return service_client.get_table_client(TABLE_NAME)


def should_send_alert(resource_group: str, cooldown_minutes: int = 60) -> bool:
    """
    Check if an alert should be sent for the given resource group,
    based on the cooldown period.

    Args:
        resource_group: Name of the resource group.
        cooldown_minutes: Minimum minutes between alerts for the same RG.

    Returns:
        True if enough time has passed since the last alert (or no prior alert exists).
    """
    try:
        table_client = _get_table_client()
        partition_key = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        row_key = resource_group.lower().replace(" ", "_")

        try:
            entity = table_client.get_entity(
                partition_key=partition_key,
                row_key=row_key,
            )
            last_alert_time = entity.get("LastAlertTime")

            if last_alert_time:
                if isinstance(last_alert_time, str):
                    last_alert_time = datetime.datetime.fromisoformat(last_alert_time)

                elapsed = datetime.datetime.utcnow() - last_alert_time
                elapsed_minutes = elapsed.total_seconds() / 60

                if elapsed_minutes < cooldown_minutes:
                    logger.info(
                        "Alert for RG '%s' skipped: last alert was %.1f min ago "
                        "(cooldown: %d min)",
                        resource_group,
                        elapsed_minutes,
                        cooldown_minutes,
                    )
                    return False

        except Exception:
            # Entity doesn't exist yet — first alert of the day
            pass

        return True

    except Exception as e:
        logger.error(
            "Error checking alert cooldown for RG '%s': %s. "
            "Allowing alert to proceed.",
            resource_group,
            str(e),
        )
        # On error, allow the alert to proceed (fail open)
        return True


def record_alert_sent(resource_group: str, current_cost: float, threshold: float) -> None:
    """
    Record that an alert was sent for the given resource group.

    Args:
        resource_group: Name of the resource group.
        current_cost: The cost that triggered the alert.
        threshold: The threshold that was exceeded.
    """
    try:
        table_client = _get_table_client()
        partition_key = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        row_key = resource_group.lower().replace(" ", "_")

        entity = {
            "PartitionKey": partition_key,
            "RowKey": row_key,
            "ResourceGroup": resource_group,
            "LastAlertTime": datetime.datetime.utcnow().isoformat(),
            "CurrentCost": current_cost,
            "Threshold": threshold,
        }

        table_client.upsert_entity(entity)
        logger.info("Recorded alert for RG '%s' at %s", resource_group, entity["LastAlertTime"])

    except Exception as e:
        logger.error(
            "Error recording alert for RG '%s': %s. "
            "Alert was still sent.",
            resource_group,
            str(e),
        )
