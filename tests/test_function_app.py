"""
Unit tests for function_app (orchestration layer).
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from function_app import _load_json_env


class TestLoadJsonEnv:
    """Tests for the _load_json_env helper."""

    def test_parses_valid_json_array(self, monkeypatch):
        """Should parse a valid JSON array from env var."""
        monkeypatch.setenv("TEST_VAR", '[{"key": "value"}]')
        result = _load_json_env("TEST_VAR")
        assert result == [{"key": "value"}]

    def test_raises_when_missing_and_no_default(self, monkeypatch):
        """Should raise ValueError when env var is missing and no default."""
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(ValueError, match="not set or empty"):
            _load_json_env("TEST_VAR")

    def test_returns_default_when_missing(self, monkeypatch):
        """Should return default when env var is missing."""
        monkeypatch.delenv("TEST_VAR", raising=False)
        result = _load_json_env("TEST_VAR", default=[])
        assert result == []

    def test_raises_on_invalid_json(self, monkeypatch):
        """Should raise ValueError on malformed JSON."""
        monkeypatch.setenv("TEST_VAR", "not valid json")
        with pytest.raises(ValueError, match="Invalid JSON"):
            _load_json_env("TEST_VAR")

    def test_raises_when_json_is_not_array(self, monkeypatch):
        """Should raise ValueError when JSON is an object, not array."""
        monkeypatch.setenv("TEST_VAR", '{"key": "value"}')
        with pytest.raises(ValueError, match="must be a JSON array"):
            _load_json_env("TEST_VAR")

    def test_parses_empty_array(self, monkeypatch):
        """Should parse an empty JSON array."""
        monkeypatch.setenv("TEST_VAR", "[]")
        result = _load_json_env("TEST_VAR")
        assert result == []


class TestTimerTrigger:
    """Tests for the timer_trigger function (integration-like)."""

    @patch("function_app.DefaultAzureCredential")
    @patch("function_app.get_daily_cost")
    @patch("function_app.send_alert")
    @patch("function_app.should_send_alert", return_value=True)
    @patch("function_app.record_alert_sent")
    def test_sends_alert_when_threshold_exceeded(
        self,
        mock_record,
        mock_should_send,
        mock_send_alert,
        mock_get_cost,
        mock_credential,
        monkeypatch,
    ):
        """Should send alert when cost exceeds threshold."""
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-123")
        monkeypatch.setenv(
            "COST_MONITOR_CONFIG",
            json.dumps([{"resource_group": "rg-test", "threshold": 10.0, "currency": "EUR"}]),
        )
        monkeypatch.setenv(
            "WEBHOOK_ENDPOINTS",
            json.dumps([{"url": "https://example.com/wh", "method": "POST"}]),
        )
        monkeypatch.setenv("ALERT_COOLDOWN_MINUTES", "60")

        mock_get_cost.return_value = 15.0  # exceeds threshold of 10.0
        mock_send_alert.return_value = [{"url": "https://example.com/wh", "success": True}]

        from function_app import timer_trigger

        mock_timer = MagicMock()
        mock_timer.past_due = False

        timer_trigger(mock_timer)

        mock_get_cost.assert_called_once()
        mock_send_alert.assert_called_once()
        mock_record.assert_called_once()

    @patch("function_app.DefaultAzureCredential")
    @patch("function_app.get_daily_cost")
    @patch("function_app.send_alert")
    def test_no_alert_when_within_threshold(
        self,
        mock_send_alert,
        mock_get_cost,
        mock_credential,
        monkeypatch,
    ):
        """Should not send alert when cost is within threshold."""
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-123")
        monkeypatch.setenv(
            "COST_MONITOR_CONFIG",
            json.dumps([{"resource_group": "rg-test", "threshold": 50.0, "currency": "EUR"}]),
        )
        monkeypatch.setenv(
            "WEBHOOK_ENDPOINTS",
            json.dumps([{"url": "https://example.com/wh", "method": "POST"}]),
        )

        mock_get_cost.return_value = 5.0  # below threshold of 50.0

        from function_app import timer_trigger

        mock_timer = MagicMock()
        mock_timer.past_due = False

        timer_trigger(mock_timer)

        mock_get_cost.assert_called_once()
        mock_send_alert.assert_not_called()

    @patch("function_app.DefaultAzureCredential")
    @patch("function_app.get_daily_cost")
    @patch("function_app.send_alert")
    @patch("function_app.should_send_alert", return_value=False)
    def test_skips_alert_during_cooldown(
        self,
        mock_should_send,
        mock_send_alert,
        mock_get_cost,
        mock_credential,
        monkeypatch,
    ):
        """Should skip alert when cooldown is active."""
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-123")
        monkeypatch.setenv(
            "COST_MONITOR_CONFIG",
            json.dumps([{"resource_group": "rg-test", "threshold": 10.0, "currency": "EUR"}]),
        )
        monkeypatch.setenv(
            "WEBHOOK_ENDPOINTS",
            json.dumps([{"url": "https://example.com/wh", "method": "POST"}]),
        )
        monkeypatch.setenv("ALERT_COOLDOWN_MINUTES", "60")

        mock_get_cost.return_value = 15.0  # exceeds threshold

        from function_app import timer_trigger

        mock_timer = MagicMock()
        mock_timer.past_due = False

        timer_trigger(mock_timer)

        mock_send_alert.assert_not_called()

    def test_aborts_without_subscription_id(self, monkeypatch):
        """Should abort gracefully without AZURE_SUBSCRIPTION_ID."""
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
        monkeypatch.setenv(
            "COST_MONITOR_CONFIG",
            json.dumps([{"resource_group": "rg-test", "threshold": 10.0}]),
        )

        from function_app import timer_trigger

        mock_timer = MagicMock()
        mock_timer.past_due = False

        # Should not raise
        timer_trigger(mock_timer)
