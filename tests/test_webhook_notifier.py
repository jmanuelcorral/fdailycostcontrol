"""
Unit tests for modules.webhook_notifier
"""

from unittest.mock import patch, MagicMock

import pytest

from modules.webhook_notifier import send_alert, build_alert_data


class TestBuildAlertData:
    """Tests for build_alert_data function."""

    def test_builds_correct_payload(self):
        """Should build a complete alert data dictionary."""
        data = build_alert_data(
            resource_group="rg-test",
            current_cost=75.0,
            threshold=50.0,
            currency="EUR",
            subscription_id="sub-123",
        )

        assert data["alert_type"] == "azure_cost_threshold_exceeded"
        assert data["resource_group"] == "rg-test"
        assert data["current_cost"] == 75.0
        assert data["threshold"] == 50.0
        assert data["currency"] == "EUR"
        assert data["subscription_id"] == "sub-123"
        assert data["exceeded_by"] == 25.0
        assert data["exceeded_by_percent"] == 50.0
        assert "timestamp" in data
        assert "date" in data

    def test_exceeded_by_percent_with_zero_threshold(self):
        """Should handle zero threshold without division error."""
        data = build_alert_data(
            resource_group="rg-test",
            current_cost=10.0,
            threshold=0.0,
            currency="EUR",
            subscription_id="sub-123",
        )

        assert data["exceeded_by_percent"] == 0.0

    def test_rounds_cost_values(self):
        """Should round cost values to 4 decimal places."""
        data = build_alert_data(
            resource_group="rg-test",
            current_cost=55.123456789,
            threshold=50.0,
            currency="EUR",
            subscription_id="sub-123",
        )

        assert data["current_cost"] == 55.1235
        assert data["exceeded_by"] == 5.1235


class TestSendAlert:
    """Tests for send_alert function."""

    @patch("modules.webhook_notifier._send_post")
    def test_sends_post_to_configured_endpoint(self, mock_post, sample_alert_data):
        """Should send POST request to endpoint configured with method POST."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        endpoints = [{"url": "https://example.com/webhook", "method": "POST"}]
        results = send_alert(endpoints, sample_alert_data)

        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["status_code"] == 200
        mock_post.assert_called_once()

    @patch("modules.webhook_notifier._send_get")
    def test_sends_get_to_configured_endpoint(self, mock_get, sample_alert_data):
        """Should send GET request to endpoint configured with method GET."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        endpoints = [{"url": "https://example.com/webhook", "method": "GET"}]
        results = send_alert(endpoints, sample_alert_data)

        assert len(results) == 1
        assert results[0]["success"] is True
        mock_get.assert_called_once()

    @patch("modules.webhook_notifier._send_post")
    def test_defaults_to_post_when_method_not_specified(self, mock_post, sample_alert_data):
        """Should default to POST when method is not specified."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        endpoints = [{"url": "https://example.com/webhook"}]
        results = send_alert(endpoints, sample_alert_data)

        assert results[0]["method"] == "POST"
        mock_post.assert_called_once()

    def test_skips_endpoint_without_url(self, sample_alert_data):
        """Should skip endpoints that don't have a URL."""
        endpoints = [{"method": "POST"}]
        results = send_alert(endpoints, sample_alert_data)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "Missing URL" in results[0]["error"]

    def test_handles_unsupported_method(self, sample_alert_data):
        """Should report error for unsupported HTTP methods."""
        endpoints = [{"url": "https://example.com/webhook", "method": "DELETE"}]
        results = send_alert(endpoints, sample_alert_data)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "Unsupported method" in results[0]["error"]

    @patch("modules.webhook_notifier._send_post")
    def test_reports_non_2xx_as_failure(self, mock_post, sample_alert_data):
        """Should report non-2xx responses as failures."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        endpoints = [{"url": "https://example.com/webhook", "method": "POST"}]
        results = send_alert(endpoints, sample_alert_data)

        assert results[0]["success"] is False
        assert results[0]["status_code"] == 500

    @patch("modules.webhook_notifier._send_post")
    def test_handles_multiple_endpoints(self, mock_post, sample_alert_data):
        """Should process all endpoints and return results for each."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        endpoints = [
            {"url": "https://example.com/wh1", "method": "POST"},
            {"url": "https://example.com/wh2", "method": "POST"},
        ]
        results = send_alert(endpoints, sample_alert_data)

        assert len(results) == 2
        assert all(r["success"] for r in results)

    @patch("modules.webhook_notifier._send_post")
    def test_continues_on_endpoint_failure(self, mock_post, sample_alert_data):
        """Should continue processing remaining endpoints when one fails."""
        import requests as req

        mock_post.side_effect = [
            req.exceptions.Timeout(),
            MagicMock(status_code=200),
        ]

        endpoints = [
            {"url": "https://example.com/wh1", "method": "POST"},
            {"url": "https://example.com/wh2", "method": "POST"},
        ]
        results = send_alert(endpoints, sample_alert_data)

        assert len(results) == 2
        assert results[0]["success"] is False
        assert results[1]["success"] is True
