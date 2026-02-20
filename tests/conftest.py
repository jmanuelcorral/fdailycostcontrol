"""
Shared pytest fixtures for fdailyCostControl tests.
"""

import pytest
import json


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure a clean environment for each test."""
    # Remove any real Azure credentials from test environment
    for var in [
        "AZURE_SUBSCRIPTION_ID",
        "COST_MONITOR_CONFIG",
        "WEBHOOK_ENDPOINTS",
        "ALERT_COOLDOWN_MINUTES",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AzureWebJobsStorage",
    ]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def sample_subscription_id():
    return "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def sample_monitor_config():
    return [
        {"resource_group": "rg-produccion", "threshold": 50.0, "currency": "EUR"},
        {"resource_group": "rg-staging", "threshold": 20.0, "currency": "EUR"},
    ]


@pytest.fixture
def sample_webhook_endpoints():
    return [
        {
            "url": "https://pandora.example.com/webhook1",
            "method": "POST",
            "headers": {"Authorization": "Bearer test-token"},
        },
        {
            "url": "https://pandora.example.com/webhook2",
            "method": "GET",
        },
    ]


@pytest.fixture
def sample_alert_data():
    return {
        "alert_type": "azure_cost_threshold_exceeded",
        "resource_group": "rg-produccion",
        "current_cost": 55.2341,
        "threshold": 50.0,
        "currency": "EUR",
        "subscription_id": "00000000-0000-0000-0000-000000000000",
        "exceeded_by": 5.2341,
        "exceeded_by_percent": 10.47,
        "timestamp": "2026-02-20T14:35:22.123456Z",
        "date": "2026-02-20",
    }


@pytest.fixture
def env_with_config(monkeypatch, sample_subscription_id, sample_monitor_config, sample_webhook_endpoints):
    """Set up environment variables with valid configuration."""
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", sample_subscription_id)
    monkeypatch.setenv("COST_MONITOR_CONFIG", json.dumps(sample_monitor_config))
    monkeypatch.setenv("WEBHOOK_ENDPOINTS", json.dumps(sample_webhook_endpoints))
    monkeypatch.setenv("ALERT_COOLDOWN_MINUTES", "60")
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")
