"""Exact account/connector routing for a dedicated Universal Entry consumer.

A registered route is necessary but never sufficient to execute. In particular
it is NOT evidence of a mounted credential, authenticated Bybit UID, current
account generation, account-scoped LIVE-arm or effective release identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConsumerWorkerRoute:
    trading_account_id: str
    account_connector_id: str

    def __post_init__(self) -> None:
        if not self.trading_account_id.strip() or not self.account_connector_id.strip():
            raise ValueError("CONSUMER_WORKER_ROUTE_IDENTITY_MISSING")


@dataclass(frozen=True, slots=True)
class ConsumerRouteResult:
    route: ConsumerWorkerRoute
    ready: bool
    reasons: tuple[str, ...]


def check_consumer_worker_route(
    connection: Any, *, route: ConsumerWorkerRoute
) -> ConsumerRouteResult:
    """Fail closed unless this registered connector belongs to exactly one account.

    No credentials, cross-account fallbacks or trade permissions are read here.
    """
    rows = connection.execute(
        """SELECT a.trading_account_id,a.venue_id,a.environment,a.status,
                  a.account_type,a.account_kind,a.external_account_ref,
                  c.account_connector_id,c.trading_account_id AS connector_account_id,
                  c.connector_kind,c.adapter_id,c.credential_ref,
                  c.status AS connector_status,c.retired_at
             FROM identity.trading_accounts a
             JOIN identity.account_connectors c
               ON c.trading_account_id=a.trading_account_id
            WHERE a.trading_account_id=%s AND c.account_connector_id=%s""",
        (route.trading_account_id, route.account_connector_id),
    ).fetchall()
    reasons: set[str] = set()
    if len(rows) != 1:
        reasons.add("ACCOUNT_CONNECTOR_ROUTE_NOT_UNIQUE")
    else:
        row: Mapping[str, object] = rows[0]
        if (
            str(row.get("trading_account_id") or "") != route.trading_account_id
            or str(row.get("connector_account_id") or "") != route.trading_account_id
            or str(row.get("account_connector_id") or "") != route.account_connector_id
        ):
            reasons.add("ACCOUNT_CONNECTOR_ROUTE_IDENTITY_MISMATCH")
        if (
            str(row.get("venue_id") or "") != "venue-bybit"
            or str(row.get("environment") or "") != "MAINNET"
            or str(row.get("account_type") or "") != "UNIFIED"
            or str(row.get("account_kind") or "") not in {"MAIN", "SUB"}
            or str(row.get("status") or "") != "ACTIVE"
        ):
            reasons.add("TRADING_ACCOUNT_ROUTE_NOT_ACTIVE")
        uid = str(row.get("external_account_ref") or "")
        if not uid.isascii() or not uid.isdecimal() or int(uid or "0") <= 0:
            reasons.add("TRADING_ACCOUNT_REGISTERED_UID_MISSING")
        if (
            str(row.get("connector_kind") or "") != "CEX_PRIVATE_RUNTIME"
            or str(row.get("adapter_id") or "") != "BYBIT_V5_PRIVATE"
            or str(row.get("connector_status") or "") != "ACTIVE"
            or row.get("retired_at") is not None
            or not str(row.get("credential_ref") or "").strip()
        ):
            reasons.add("ACCOUNT_PRIVATE_CONNECTOR_NOT_ACTIVE")
    return ConsumerRouteResult(route, not reasons, tuple(sorted(reasons)))
