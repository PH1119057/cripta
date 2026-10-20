"""Read-only registered account/connector evidence at an exact DB snapshot.

Registration evidence alone does not authorize LIVE or prove private Exchange
connectivity, order support or protective-order capability.
"""
from __future__ import annotations

from typing import Any

from bybit_workbench.account_connector_evidence import (
    AccountConnectorEvidence,
    check_account_connector_evidence,
)


def read_account_connector_evidence(
    connection: Any, *, required_account_ids: tuple[str, ...]
) -> AccountConnectorEvidence:
    """Query registration data only, without interpolating user IDs into SQL."""
    account_ids = tuple(str(value or "").strip() for value in required_account_ids)
    if not account_ids or any(not value for value in account_ids):
        return check_account_connector_evidence(
            required_account_ids=account_ids, accounts=[], connectors=[]
        )
    account_rows = connection.execute(
        """SELECT trading_account_id,venue_id,status,environment
             FROM identity.trading_accounts
            WHERE trading_account_id = ANY(%s)""",
        (list(account_ids),),
    ).fetchall()
    connector_rows = connection.execute(
        """SELECT account_connector_id,trading_account_id,adapter_id,
                  credential_ref,status
             FROM identity.account_connectors
            WHERE trading_account_id = ANY(%s)""",
        (list(account_ids),),
    ).fetchall()
    account_fields = ("trading_account_id", "venue_id", "status", "environment")
    connector_fields = (
        "account_connector_id", "trading_account_id", "adapter_id",
        "credential_ref", "status",
    )
    return check_account_connector_evidence(
        required_account_ids=account_ids,
        accounts=[dict(zip(account_fields, row, strict=True)) for row in account_rows],
        connectors=[dict(zip(connector_fields, row, strict=True)) for row in connector_rows],
    )
