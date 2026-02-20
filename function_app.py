import azure.functions as func
import datetime
import json
import logging
import os

from azure.identity import DefaultAzureCredential

from modules.cost_client import get_daily_cost
from modules.webhook_notifier import send_alert, build_alert_data
from modules.alert_tracker import should_send_alert, record_alert_sent

app = func.FunctionApp()

logger = logging.getLogger(__name__)


def _load_json_env(var_name: str, default: list | None = None) -> list:
    """Load and parse a JSON array from an environment variable."""
    raw = os.environ.get(var_name, "")
    if not raw:
        if default is not None:
            return default
        raise ValueError(f"Environment variable '{var_name}' is not set or empty.")
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError(f"'{var_name}' must be a JSON array, got {type(parsed).__name__}.")
        return parsed
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in '{var_name}': {e}")


@app.timer_trigger(
    schedule="0 */5 * * * *",
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=False,
)
def timer_trigger(myTimer: func.TimerRequest) -> None:
    """
    Azure Function that runs every 5 minutes to monitor Azure costs
    per Resource Group and sends webhook alerts to Pandora FMS
    when thresholds are exceeded.
    """
    utc_now = datetime.datetime.utcnow().isoformat() + "Z"

    if myTimer.past_due:
        logger.warning("Timer is past due! Current time: %s", utc_now)

    logger.info("Cost monitoring function started at %s", utc_now)

    # --- Load configuration ---
    try:
        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
        if not subscription_id:
            logger.error("AZURE_SUBSCRIPTION_ID is not configured. Aborting.")
            return

        monitor_config = _load_json_env("COST_MONITOR_CONFIG")
        webhook_endpoints = _load_json_env("WEBHOOK_ENDPOINTS")
        cooldown_minutes = int(os.environ.get("ALERT_COOLDOWN_MINUTES", "60"))

    except (ValueError, TypeError) as e:
        logger.error("Configuration error: %s", str(e))
        return

    if not monitor_config:
        logger.warning("No resource groups configured for monitoring. Nothing to do.")
        return

    if not webhook_endpoints:
        logger.warning("No webhook endpoints configured. Alerts cannot be sent.")
        return

    logger.info(
        "Monitoring %d resource group(s), %d webhook endpoint(s), cooldown=%d min",
        len(monitor_config),
        len(webhook_endpoints),
        cooldown_minutes,
    )

    # --- Authenticate ---
    try:
        credential = DefaultAzureCredential()
        logger.info("Azure credential initialized successfully.")
    except Exception as e:
        logger.error("Failed to initialize Azure credential: %s", str(e))
        return

    # --- Process each Resource Group ---
    alerts_sent = 0
    alerts_skipped_cooldown = 0
    errors = 0

    for rg_config in monitor_config:
        resource_group = rg_config.get("resource_group")
        threshold = rg_config.get("threshold", 0.0)
        currency = rg_config.get("currency", "EUR")

        if not resource_group:
            logger.warning("Config entry missing 'resource_group', skipping: %s", rg_config)
            continue

        logger.info(
            "Checking RG '%s' — threshold: %.2f %s",
            resource_group,
            threshold,
            currency,
        )

        try:
            # Query current daily cost
            current_cost = get_daily_cost(credential, subscription_id, resource_group)

            logger.info(
                "RG '%s': current cost = %.4f %s, threshold = %.2f %s",
                resource_group,
                current_cost,
                currency,
                threshold,
                currency,
            )

            # Check if threshold is exceeded
            if current_cost > threshold:
                logger.warning(
                    "THRESHOLD EXCEEDED for RG '%s': %.4f > %.2f %s",
                    resource_group,
                    current_cost,
                    threshold,
                    currency,
                )

                # Check cooldown before sending alert
                if not should_send_alert(resource_group, cooldown_minutes):
                    alerts_skipped_cooldown += 1
                    continue

                # Build alert payload and send to all endpoints
                alert_data = build_alert_data(
                    resource_group=resource_group,
                    current_cost=current_cost,
                    threshold=threshold,
                    currency=currency,
                    subscription_id=subscription_id,
                )

                results = send_alert(webhook_endpoints, alert_data)

                # Check if at least one endpoint succeeded
                any_success = any(r.get("success", False) for r in results)
                if any_success:
                    record_alert_sent(resource_group, current_cost, threshold)
                    alerts_sent += 1
                    logger.info(
                        "Alert sent for RG '%s'. Results: %s",
                        resource_group,
                        json.dumps(results, default=str),
                    )
                else:
                    errors += 1
                    logger.error(
                        "All webhook endpoints failed for RG '%s'. Results: %s",
                        resource_group,
                        json.dumps(results, default=str),
                    )
            else:
                logger.info(
                    "RG '%s': cost %.4f is within threshold %.2f %s. No alert needed.",
                    resource_group,
                    current_cost,
                    threshold,
                    currency,
                )

        except Exception as e:
            errors += 1
            logger.error(
                "Error processing RG '%s': %s",
                resource_group,
                str(e),
            )

    # --- Summary ---
    logger.info(
        "Cost monitoring completed. Alerts sent: %d, Skipped (cooldown): %d, Errors: %d",
        alerts_sent,
        alerts_skipped_cooldown,
        errors,
    )