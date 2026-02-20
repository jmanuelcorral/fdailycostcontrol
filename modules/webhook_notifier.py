"""
Module to send alert notifications to webhook endpoints (Pandora FMS).
Supports configurable HTTP methods (GET/POST) per endpoint.
"""

import logging
import datetime
import requests

logger = logging.getLogger(__name__)


def send_alert(endpoints_config: list, alert_data: dict) -> list:
    """
    Send alert notifications to all configured webhook endpoints.

    Args:
        endpoints_config: List of endpoint configurations, each containing:
            - url (str): The webhook URL.
            - method (str): HTTP method ("GET" or "POST").
            - headers (dict, optional): Additional HTTP headers.
        alert_data: Dictionary with alert details:
            - resource_group (str)
            - current_cost (float)
            - threshold (float)
            - currency (str)
            - subscription_id (str)
            - timestamp (str)

    Returns:
        List of results with status per endpoint.
    """
    results = []

    for endpoint in endpoints_config:
        url = endpoint.get("url")
        method = endpoint.get("method", "POST").upper()
        headers = endpoint.get("headers", {})

        if not url:
            logger.warning("Endpoint configuration missing 'url', skipping.")
            results.append({"url": None, "success": False, "error": "Missing URL"})
            continue

        try:
            if method == "POST":
                response = _send_post(url, headers, alert_data)
            elif method == "GET":
                response = _send_get(url, headers, alert_data)
            else:
                logger.warning(
                    "Unsupported HTTP method '%s' for endpoint '%s', skipping.",
                    method,
                    url,
                )
                results.append(
                    {"url": url, "success": False, "error": f"Unsupported method: {method}"}
                )
                continue

            success = 200 <= response.status_code < 300
            result = {
                "url": url,
                "method": method,
                "success": success,
                "status_code": response.status_code,
            }

            if success:
                logger.info(
                    "Alert sent successfully to %s [%s] -> HTTP %d",
                    url,
                    method,
                    response.status_code,
                )
            else:
                result["response_body"] = response.text[:500]
                logger.warning(
                    "Alert to %s [%s] returned HTTP %d: %s",
                    url,
                    method,
                    response.status_code,
                    response.text[:200],
                )

            results.append(result)

        except requests.exceptions.Timeout:
            logger.error("Timeout sending alert to %s", url)
            results.append({"url": url, "success": False, "error": "Timeout"})

        except requests.exceptions.ConnectionError:
            logger.error("Connection error sending alert to %s", url)
            results.append({"url": url, "success": False, "error": "Connection error"})

        except Exception as e:
            logger.error("Unexpected error sending alert to %s: %s", url, str(e))
            results.append({"url": url, "success": False, "error": str(e)})

    return results


def _send_post(url: str, headers: dict, alert_data: dict) -> requests.Response:
    """Send a POST request with JSON body."""
    default_headers = {"Content-Type": "application/json"}
    default_headers.update(headers)

    return requests.post(
        url,
        json=alert_data,
        headers=default_headers,
        timeout=30,
    )


def _send_get(url: str, headers: dict, alert_data: dict) -> requests.Response:
    """Send a GET request with alert data as query parameters."""
    # Flatten alert_data for query params (convert nested values to strings)
    params = {k: str(v) for k, v in alert_data.items()}

    return requests.get(
        url,
        params=params,
        headers=headers,
        timeout=30,
    )


def build_alert_data(
    resource_group: str,
    current_cost: float,
    threshold: float,
    currency: str,
    subscription_id: str,
) -> dict:
    """
    Build the alert data payload.

    Returns:
        Dictionary with all alert fields ready to send.
    """
    return {
        "alert_type": "azure_cost_threshold_exceeded",
        "resource_group": resource_group,
        "current_cost": round(current_cost, 4),
        "threshold": threshold,
        "currency": currency,
        "subscription_id": subscription_id,
        "exceeded_by": round(current_cost - threshold, 4),
        "exceeded_by_percent": round(
            ((current_cost - threshold) / threshold) * 100, 2
        )
        if threshold > 0
        else 0.0,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "date": datetime.datetime.utcnow().date().isoformat(),
    }
