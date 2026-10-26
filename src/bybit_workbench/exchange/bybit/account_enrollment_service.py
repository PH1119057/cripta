"""Signed AccountConnector enrollment prior to Strategy financial-account selection.

Only an approved credential-mounted caller supplies signed_account_get. This module
neither reads secrets nor opens HTTP, grants LIVE, or modifies a StrategyCard.
The caller owns authorization, transaction, audit and commit boundaries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.account_uid_registration_gate import registered_uid_match
from bybit_workbench.exchange.bybit.account_identity_snapshot import (
    normalize_authenticated_api_key_info,
)


@dataclass(frozen=True, slots=True)
class VerifiedAccountEnrollment:
    trading_account_id: str
    account_connector_id: str
    observed_at: datetime
    verified_credential_ref: str = field(repr=False)
    provider_uid: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class EnrollmentCheck:
    ready: bool
    reasons: tuple[str, ...]
    evidence: VerifiedAccountEnrollment | None = field(default=None, repr=False)


_ACCOUNT_COLUMNS = (
    "trading_account_id", "venue_id", "external_account_ref",
    "parent_trading_account_id", "account_kind", "account_type",
    "environment", "status",
)
_CONNECTOR_COLUMNS = (
    "account_connector_id", "trading_account_id", "connector_kind",
    "adapter_id", "credential_ref", "status", "retired_at",
)


def _uid(raw: object) -> str:
    if type(raw) not in (str, int):
        return ""
    value = str(raw)
    if not value or not value.isascii() or not value.isdecimal() or int(value) <= 0:
        return ""
    return str(int(value))


def _rows(connection: Any) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    accounts = connection.execute(
        """SELECT trading_account_id,venue_id,external_account_ref,
                  parent_trading_account_id,account_kind,account_type,
                  environment,status
             FROM identity.trading_accounts
            WHERE venue_id='venue-bybit' AND environment='MAINNET'"""
    ).fetchall()
    connectors = connection.execute(
        """SELECT account_connector_id,trading_account_id,connector_kind,
                  adapter_id,credential_ref,status,retired_at
             FROM identity.account_connectors
            WHERE adapter_id='BYBIT_V5_PRIVATE'"""
    ).fetchall()
    return (
        [dict(zip(_ACCOUNT_COLUMNS, row, strict=True)) for row in accounts],
        [dict(zip(_CONNECTOR_COLUMNS, row, strict=True)) for row in connectors],
    )


def verify_account_enrollment(
    connection: Any,
    *,
    trading_account_id: str,
    account_connector_id: str,
    proposed_provider_uid: str,
    mounted_credential_ref: str,
    signed_account_get: Callable[[], Mapping[str, object]],
    now: datetime,
    max_age_seconds: int = 15,
) -> EnrollmentCheck:
    """Verify prospective UID with fresh signed Bybit evidence, without DB writes.

    Unlike an Entry-time check, this permits a NULL registered UID for the
    *exact* account being enrolled. No other account can be relabeled.
    The callback must be bound to the independently verified mounted credential;
    a DB-provided credential_ref is not sufficient.
    """
    uid = _uid(proposed_provider_uid)
    if not uid:
        return EnrollmentCheck(False, ("PROPOSED_BYBIT_UID_INVALID",))
    if now.tzinfo is None or now.utcoffset() is None or max_age_seconds <= 0:
        return EnrollmentCheck(False, ("ATTESTATION_TIME_INVALID",))
    if not trading_account_id or not account_connector_id or not mounted_credential_ref:
        return EnrollmentCheck(False, ("MOUNTED_ACCOUNT_CONNECTOR_IDENTITY_MISSING",))
    accounts, connectors = _rows(connection)
    targets = [r for r in accounts if r["trading_account_id"] == trading_account_id]
    own = [
        r for r in connectors
        if r["account_connector_id"] == account_connector_id
        and r["trading_account_id"] == trading_account_id
        and r["credential_ref"] == mounted_credential_ref
        and r["connector_kind"] == "CEX_PRIVATE_RUNTIME"
        and r["status"] == "ACTIVE"
        and r["retired_at"] is None
    ]
    if len(targets) != 1 or len(own) != 1:
        return EnrollmentCheck(False, ("ACCOUNT_CONNECTOR_REGISTRATION_OR_MOUNT_MISMATCH",))
    current_uid = _uid(targets[0]["external_account_ref"])
    if targets[0]["external_account_ref"] is not None and current_uid != uid:
        return EnrollmentCheck(False, ("REGISTERED_BYBIT_UID_MISMATCH",))
    try:
        raw = signed_account_get()
        snapshot = normalize_authenticated_api_key_info(
            raw, observed_at=now.astimezone(UTC), signed_get_proven=True
        )
    except Exception:
        # Never allow provider payloads, signed URL, key IDs or IPs into logs.
        return EnrollmentCheck(False, ("AUTHENTICATED_EXCHANGE_READ_FAILED",))
    if snapshot["account_user_id"] != uid:
        return EnrollmentCheck(False, ("PROPOSED_BYBIT_UID_MISMATCH",))

    prospective = [
        {**row, "external_account_ref": uid}
        if row["trading_account_id"] == trading_account_id else row
        for row in accounts
    ]
    verified = registered_uid_match(
        snapshot=snapshot,
        trading_account_id=trading_account_id,
        account_connector_id=account_connector_id,
        accounts=prospective,
        connectors=connectors,
        now=now,
        max_age_seconds=max_age_seconds,
        verified_credential_ref=mounted_credential_ref,
    )
    if not verified.matches:
        return EnrollmentCheck(False, verified.reasons)
    return EnrollmentCheck(
        True, (),
        VerifiedAccountEnrollment(
            trading_account_id, account_connector_id, now.astimezone(UTC),
            mounted_credential_ref, uid,
        ),
    )


def persist_verified_account_enrollment(
    connection: Any,
    *,
    evidence: VerifiedAccountEnrollment,
    now: datetime,
    approved_operator_ref: str,
) -> None:
    """Conditional registry update inside an externally authorized transaction.

    Does not COMMIT. The caller must prove operator authorization and use a
    SERIALIZABLE transaction so a competing enrollment cannot silently create
    the same UID. No Strategy binding or LIVE gate is altered.
    """
    if not approved_operator_ref.strip():
        raise RuntimeError("ACCOUNT_ENROLLMENT_OWNER_APPROVAL_MISSING")
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeError("ACCOUNT_ENROLLMENT_TIME_INVALID")
    if not 0 <= (now.astimezone(UTC) - evidence.observed_at).total_seconds() <= 15:
        raise RuntimeError("ACCOUNT_ENROLLMENT_ATTESTATION_STALE")
    level = connection.execute("SHOW transaction_isolation").fetchone()
    if level is None or str(level[0]).lower() != "serializable":
        raise RuntimeError("ACCOUNT_ENROLLMENT_SERIALIZABLE_REQUIRED")
    if not evidence.verified_credential_ref or not _uid(evidence.provider_uid):
        raise RuntimeError("ACCOUNT_ENROLLMENT_EVIDENCE_INVALID")
    updated = connection.execute(
        """UPDATE identity.trading_accounts a
              SET external_account_ref=%s
            WHERE a.trading_account_id=%s
              AND a.venue_id='venue-bybit'
              AND a.environment='MAINNET' AND a.status='ACTIVE'
              AND (a.external_account_ref IS NULL OR a.external_account_ref=%s)
              AND EXISTS (
                  SELECT 1 FROM identity.account_connectors c
                   WHERE c.trading_account_id=a.trading_account_id
                     AND c.account_connector_id=%s
                     AND c.connector_kind='CEX_PRIVATE_RUNTIME'
                     AND c.adapter_id='BYBIT_V5_PRIVATE'
                     AND c.credential_ref=%s
                     AND c.status='ACTIVE' AND c.retired_at IS NULL
              )
              AND NOT EXISTS (
                  SELECT 1 FROM identity.trading_accounts other
                   WHERE other.trading_account_id<>a.trading_account_id
                     AND other.venue_id='venue-bybit'
                     AND other.environment='MAINNET'
                     AND other.status='ACTIVE'
                     AND other.external_account_ref=%s
              )
          RETURNING a.trading_account_id""",
        (
            evidence.provider_uid, evidence.trading_account_id,
            evidence.provider_uid, evidence.account_connector_id,
            evidence.verified_credential_ref, evidence.provider_uid,
        ),
    ).fetchall()
    if len(updated) != 1:
        raise RuntimeError("ACCOUNT_ENROLLMENT_CONCURRENT_REGISTRY_CHANGE")
