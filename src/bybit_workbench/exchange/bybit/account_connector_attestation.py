"""Read-only signed Bybit account identity attestation for one exact connector.

A successful registry lookup alone is never identity proof. No raw provider
response, credentials, API key, IP address or UID is persisted or logged here.
The credential reference MUST be independently obtained from the credential
mounted for this worker; taking it from the DB would defeat the comparison.
This attestation grants no LIVE arm, release right or exchange mutation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.account_uid_registration_gate import registered_uid_match
from bybit_workbench.exchange.bybit.account_identity_snapshot import (
    normalize_authenticated_api_key_info,
)


@dataclass(frozen=True, slots=True)
class AuthenticatedConnectorAttestation:
    ready: bool
    reasons: tuple[str, ...]


def _attestation_row(
    row: object, columns: tuple[str, ...],
) -> dict[str, object]:
    """Support explicit psycopg dict_row as well as existing tuple rows."""
    if isinstance(row, Mapping):
        try:
            return {column: row[column] for column in columns}
        except KeyError:
            return {}
    if isinstance(row, (tuple, list)) and len(row) == len(columns):
        return dict(zip(columns, row, strict=True))
    return {}


def attest_bybit_account_connector(
    connection: Any,
    *,
    trading_account_id: str,
    account_connector_id: str,
    verified_credential_ref: str | None,
    signed_account_get: Callable[[], Mapping[str, object]],
    now: datetime,
    max_age_seconds: int = 15,
) -> AuthenticatedConnectorAttestation:
    """Check the current credential's signed API-key identity against registry.

    signed_account_get must be bound to *this worker's mounted* credential and
    perform the authenticated Bybit GET /v5/user/query-api. Its transport
    trust must be established by its caller, not by an arbitrary callback.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        return AuthenticatedConnectorAttestation(False, ("ATTESTATION_TIME_INVALID",))
    if not trading_account_id or not account_connector_id:
        return AuthenticatedConnectorAttestation(False, ("REQUIRED_IDENTITY_MISSING",))
    if not isinstance(verified_credential_ref, str) or not verified_credential_ref.strip():
        return AuthenticatedConnectorAttestation(
            False, ("VERIFIED_CONNECTOR_CREDENTIAL_PROVENANCE_MISSING",)
        )
    if not isinstance(max_age_seconds, int) or isinstance(max_age_seconds, bool):
        return AuthenticatedConnectorAttestation(False, ("ATTESTATION_MAX_AGE_INVALID",))
    if max_age_seconds <= 0:
        return AuthenticatedConnectorAttestation(False, ("ATTESTATION_MAX_AGE_INVALID",))
    accounts_cursor = connection.execute(
        """SELECT trading_account_id,venue_id,external_account_ref,
                  parent_trading_account_id,account_kind,account_type,
                  environment,status
             FROM identity.trading_accounts
            WHERE venue_id='venue-bybit' AND environment='MAINNET'""",
    )
    accounts = [
        _attestation_row(
            row,
            (
                "trading_account_id", "venue_id", "external_account_ref",
                "parent_trading_account_id", "account_kind", "account_type",
                "environment", "status",
            ),
        )
        for row in accounts_cursor.fetchall()
    ]
    connectors_cursor = connection.execute(
        """SELECT account_connector_id,trading_account_id,
                  connector_kind,adapter_id,credential_ref,status,retired_at
             FROM identity.account_connectors
            WHERE account_connector_id=%s""",
        (account_connector_id,),
    )
    connectors = [
        _attestation_row(
            row,
            (
                "account_connector_id", "trading_account_id",
                "connector_kind", "adapter_id", "credential_ref",
                "status", "retired_at",
            ),
        )
        for row in connectors_cursor.fetchall()
    ]
    # Do not invoke signed transport if registry or credential provenance is
    # not exact. The provider call must never be made for a different worker.
    matches = [
        item for item in accounts
        if item["trading_account_id"] == trading_account_id and item["status"] == "ACTIVE"
    ]
    live_connectors = [
        item for item in connectors
        if (
            item["trading_account_id"] == trading_account_id
            and item["status"] == "ACTIVE"
            and item["connector_kind"] == "CEX_PRIVATE_RUNTIME"
            and item["adapter_id"] == "BYBIT_V5_PRIVATE"
            and item["retired_at"] is None
            and item["credential_ref"] == verified_credential_ref
        )
    ]
    if len(matches) != 1 or len(live_connectors) != 1:
        return AuthenticatedConnectorAttestation(
            False, ("ACCOUNT_CONNECTOR_REGISTRATION_OR_MOUNT_MISMATCH",)
        )
    try:
        raw = signed_account_get()
        snapshot = normalize_authenticated_api_key_info(
            raw, observed_at=now.astimezone(UTC), signed_get_proven=True
        )
    except Exception:
        # Raw exception or response may contain signed URL, API key or IPs.
        return AuthenticatedConnectorAttestation(False, ("AUTHENTICATED_EXCHANGE_READ_FAILED",))
    result = registered_uid_match(
        snapshot=snapshot,
        trading_account_id=trading_account_id,
        account_connector_id=account_connector_id,
        accounts=accounts,
        connectors=connectors,
        now=now,
        max_age_seconds=max_age_seconds,
        verified_credential_ref=verified_credential_ref,
    )
    return AuthenticatedConnectorAttestation(result.matches, result.reasons)
