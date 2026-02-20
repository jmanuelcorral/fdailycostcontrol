"""
Unit tests for modules.cost_client
"""

from unittest.mock import MagicMock, patch

import pytest

from modules.cost_client import get_daily_cost


class TestGetDailyCost:
    """Tests for get_daily_cost function."""

    @patch("modules.cost_client.CostManagementClient")
    def test_returns_total_cost_from_single_row(self, mock_client_cls):
        """Should return the cost value from a single result row."""
        mock_result = MagicMock()
        mock_result.rows = [[42.50, "EUR", "rg-produccion"]]
        mock_client_cls.return_value.query.usage.return_value = mock_result

        credential = MagicMock()
        cost = get_daily_cost(credential, "sub-123", "rg-produccion")

        assert cost == 42.50

    @patch("modules.cost_client.CostManagementClient")
    def test_returns_sum_of_multiple_rows(self, mock_client_cls):
        """Should sum costs across multiple result rows."""
        mock_result = MagicMock()
        mock_result.rows = [
            [10.00, "EUR", "rg-produccion"],
            [25.50, "EUR", "rg-produccion"],
        ]
        mock_client_cls.return_value.query.usage.return_value = mock_result

        credential = MagicMock()
        cost = get_daily_cost(credential, "sub-123", "rg-produccion")

        assert cost == 35.50

    @patch("modules.cost_client.CostManagementClient")
    def test_returns_zero_when_no_rows(self, mock_client_cls):
        """Should return 0.0 when there are no cost data rows."""
        mock_result = MagicMock()
        mock_result.rows = []
        mock_client_cls.return_value.query.usage.return_value = mock_result

        credential = MagicMock()
        cost = get_daily_cost(credential, "sub-123", "rg-produccion")

        assert cost == 0.0

    @patch("modules.cost_client.CostManagementClient")
    def test_returns_zero_when_rows_is_none(self, mock_client_cls):
        """Should return 0.0 when rows is None."""
        mock_result = MagicMock()
        mock_result.rows = None
        mock_client_cls.return_value.query.usage.return_value = mock_result

        credential = MagicMock()
        cost = get_daily_cost(credential, "sub-123", "rg-produccion")

        assert cost == 0.0

    @patch("modules.cost_client.CostManagementClient")
    def test_raises_on_api_error(self, mock_client_cls):
        """Should propagate exceptions from the Cost Management API."""
        mock_client_cls.return_value.query.usage.side_effect = Exception("API error")

        credential = MagicMock()

        with pytest.raises(Exception, match="API error"):
            get_daily_cost(credential, "sub-123", "rg-produccion")

    @patch("modules.cost_client.CostManagementClient")
    def test_uses_correct_scope(self, mock_client_cls):
        """Should query with the correct subscription scope."""
        mock_result = MagicMock()
        mock_result.rows = []
        mock_client_cls.return_value.query.usage.return_value = mock_result

        credential = MagicMock()
        get_daily_cost(credential, "my-sub-id", "rg-test")

        call_kwargs = mock_client_cls.return_value.query.usage.call_args
        assert call_kwargs.kwargs["scope"] == "/subscriptions/my-sub-id"
