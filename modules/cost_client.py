"""
Module to query Azure Cost Management API for daily costs per Resource Group.
Uses the azure-mgmt-costmanagement SDK with DefaultAzureCredential.
"""

import logging
import datetime
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryDefinition,
    QueryDataset,
    QueryTimePeriod,
    QueryAggregation,
    QueryGrouping,
    QueryFilter,
    QueryComparisonExpression,
)

logger = logging.getLogger(__name__)


def get_daily_cost(credential, subscription_id: str, resource_group: str) -> float:
    """
    Query Azure Cost Management for the accumulated cost of a Resource Group
    for the current day (00:00 UTC to now).

    Args:
        credential: Azure credential (DefaultAzureCredential).
        subscription_id: Azure subscription ID.
        resource_group: Name of the resource group to query.

    Returns:
        Total cost as a float. Returns 0.0 if no cost data is found.
    """
    client = CostManagementClient(credential)

    today = datetime.datetime.utcnow().date()
    time_from = datetime.datetime(today.year, today.month, today.day, 0, 0, 0)
    time_to = datetime.datetime.utcnow()

    scope = f"/subscriptions/{subscription_id}"

    query_definition = QueryDefinition(
        type="ActualCost",
        timeframe="Custom",
        time_period=QueryTimePeriod(
            from_property=time_from,
            to=time_to,
        ),
        dataset=QueryDataset(
            granularity="None",
            aggregation={
                "totalCost": QueryAggregation(
                    name="Cost",
                    function="Sum",
                ),
            },
            grouping=[
                QueryGrouping(
                    type="Dimension",
                    name="ResourceGroup",
                ),
            ],
            filter=QueryFilter(
                dimensions=QueryComparisonExpression(
                    name="ResourceGroup",
                    operator="In",
                    values=[resource_group],
                ),
            ),
        ),
    )

    try:
        result = client.query.usage(scope=scope, parameters=query_definition)

        if not result.rows:
            logger.info(
                "No cost data found for RG '%s' on %s", resource_group, today.isoformat()
            )
            return 0.0

        # The result rows contain [cost, currency, resource_group_name]
        total_cost = 0.0
        for row in result.rows:
            total_cost += float(row[0])

        logger.info(
            "Cost for RG '%s' on %s: %.4f",
            resource_group,
            today.isoformat(),
            total_cost,
        )
        return total_cost

    except Exception as e:
        logger.error(
            "Error querying cost for RG '%s': %s", resource_group, str(e)
        )
        raise
