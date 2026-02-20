"""
Unit tests for modules.alert_tracker
"""

import datetime
from unittest.mock import patch, MagicMock

import pytest

from modules.alert_tracker import should_send_alert, record_alert_sent


class TestShouldSendAlert:
    """Tests for should_send_alert function."""

    @patch("modules.alert_tracker._get_table_client")
    def test_returns_true_when_no_prior_alert(self, mock_table_client):
        """Should return True when no prior alert entity exists (first alert)."""
        mock_client = MagicMock()
        mock_client.get_entity.side_effect = Exception("ResourceNotFoundError")
        mock_table_client.return_value = mock_client

        result = should_send_alert("rg-test", cooldown_minutes=60)

        assert result is True

    @patch("modules.alert_tracker._get_table_client")
    def test_returns_false_during_cooldown(self, mock_table_client):
        """Should return False when last alert was within cooldown period."""
        mock_client = MagicMock()
        recent_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
        mock_client.get_entity.return_value = {
            "LastAlertTime": recent_time.isoformat(),
        }
        mock_table_client.return_value = mock_client

        result = should_send_alert("rg-test", cooldown_minutes=60)

        assert result is False

    @patch("modules.alert_tracker._get_table_client")
    def test_returns_true_after_cooldown_expired(self, mock_table_client):
        """Should return True when cooldown has expired."""
        mock_client = MagicMock()
        old_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=120)
        mock_client.get_entity.return_value = {
            "LastAlertTime": old_time.isoformat(),
        }
        mock_table_client.return_value = mock_client

        result = should_send_alert("rg-test", cooldown_minutes=60)

        assert result is True

    @patch("modules.alert_tracker._get_table_client")
    def test_returns_true_on_table_storage_error(self, mock_table_client):
        """Should fail open (return True) when Table Storage is unavailable."""
        mock_table_client.side_effect = Exception("Storage unavailable")

        result = should_send_alert("rg-test", cooldown_minutes=60)

        assert result is True

    @patch("modules.alert_tracker._get_table_client")
    def test_returns_true_when_last_alert_time_is_none(self, mock_table_client):
        """Should return True when entity exists but LastAlertTime is None."""
        mock_client = MagicMock()
        mock_client.get_entity.return_value = {"LastAlertTime": None}
        mock_table_client.return_value = mock_client

        result = should_send_alert("rg-test", cooldown_minutes=60)

        assert result is True


class TestRecordAlertSent:
    """Tests for record_alert_sent function."""

    @patch("modules.alert_tracker._get_table_client")
    def test_upserts_entity_to_table(self, mock_table_client):
        """Should upsert an entity with correct fields to Table Storage."""
        mock_client = MagicMock()
        mock_table_client.return_value = mock_client

        record_alert_sent("rg-produccion", 55.23, 50.0)

        mock_client.upsert_entity.assert_called_once()
        entity = mock_client.upsert_entity.call_args[0][0]

        assert entity["ResourceGroup"] == "rg-produccion"
        assert entity["CurrentCost"] == 55.23
        assert entity["Threshold"] == 50.0
        assert entity["RowKey"] == "rg-produccion"
        assert "LastAlertTime" in entity

    @patch("modules.alert_tracker._get_table_client")
    def test_does_not_raise_on_storage_error(self, mock_table_client):
        """Should not propagate exceptions if Table Storage fails."""
        mock_client = MagicMock()
        mock_client.upsert_entity.side_effect = Exception("Storage error")
        mock_table_client.return_value = mock_client

        # Should NOT raise
        record_alert_sent("rg-test", 10.0, 5.0)

    @patch("modules.alert_tracker._get_table_client")
    def test_uses_today_as_partition_key(self, mock_table_client):
        """Should use today's date as PartitionKey."""
        mock_client = MagicMock()
        mock_table_client.return_value = mock_client

        record_alert_sent("rg-test", 10.0, 5.0)

        entity = mock_client.upsert_entity.call_args[0][0]
        expected_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        assert entity["PartitionKey"] == expected_date
