"""Independent read-only identity/exposure bridge for Lifecycle Supervisor.

No DB mutation and no Exchange transport here. Input Exchange evidence is
obtained by protection_probe. A *single SQL statement* observes registry,
claims and open owners in one PostgreSQL statement-level MVCC snapshot.
Only a matching authenticated UID+connector may produce verified exposure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bybit_workbench.account_uid_registration_gate import registered_uid_match
from bybit_workbench.exchange.bybit.exposure_audit import (
    ExposureInventoryUnavailable,
    audit_account_exposure,
)


@dataclass(frozen=True, slots=True)
class ExposureAuditResult:
    # None: unavailable/unproven. Empty tuple: verified account is flat.
    verified_positions: tuple[dict[str, object], ...] | None
    state: str
    reasons: tuple[str, ...]
    unknown_open_count: int = 0


# One SQL statement, not four READ COMMITTED SELECT snapshots. JSONB must be
# adapted to Python list[dict] by the production psycopg JSON decoder.
_ACCOUNT_LINEAGE_SQL = """
SELECT
  (SELECT coalesce(jsonb_agg(to_jsonb(a)), '[]'::jsonb)
     FROM (SELECT trading_account_id,venue_id,external_account_ref,
                  parent_trading_account_id,account_kind,account_type,
                  environment,status
             FROM identity.trading_accounts
            WHERE venue_id='venue-bybit' AND environment='MAINNET') a) AS accounts,
  (SELECT coalesce(jsonb_agg(to_jsonb(c)), '[]'::jsonb)
     FROM (SELECT account_connector_id,trading_account_id,adapter_id,
                  credential_ref,status
             FROM identity.account_connectors
            WHERE trading_account_id=%s) c) AS connectors,
  (SELECT coalesce(jsonb_agg(to_jsonb(s)), '[]'::jsonb)
     FROM (SELECT exchange_position_slot_claim_id,trading_account_id,
                  identity_status,claim_state,symbol,position_idx,
                  exchange_position_key,strategy_position_id
             FROM runtime.exchange_position_slot_claims
            WHERE trading_account_id=%s AND identity_status='PROVEN'
              AND claim_state IN ('CLAIMED','BOUND','RECONCILIATION_REQUIRED')) s)
     AS active_claims,
  (SELECT coalesce(jsonb_agg(to_jsonb(p)), '[]'::jsonb)
     FROM (SELECT position_id,trading_account_id,identity_status,state,
                  symbol,position_idx,exchange_position_key,side
             FROM runtime.position_ownership
            WHERE trading_account_id=%s AND identity_status='PROVEN'
              AND state='OPEN') p) AS open_owners
"""


def _json_rows(row: Mapping[str, object], name: str) -> list[Mapping[str, object]]:
    value = row.get(name)
    if not isinstance(value, list) or any(not isinstance(x, Mapping) for x in value):
        raise ExposureInventoryUnavailable("lineage snapshot invalid: " + name)
    return value


def verified_account_exposure(
    connection: Any,
    *,
    account_snapshot: Mapping[str, object] | None,
    trading_account_id: str,
    account_connector_id: str,
    verified_credential_ref: str | None,
    now: datetime,
    max_age_seconds: int = 15,
) -> ExposureAuditResult:
    """Return verified position/claim rows, or None (never false healthy)."""
    if not trading_account_id:
        return ExposureAuditResult(None, "IDENTITY_UNKNOWN", ("ACCOUNT_IDENTITY_MISSING",))
    if not isinstance(account_snapshot, Mapping):
        return ExposureAuditResult(
            None, "EXCHANGE_UNKNOWN", ("AUTHENTICATED_ACCOUNT_READ_MISSING",)
        )
    try:
        row = connection.execute(
            _ACCOUNT_LINEAGE_SQL,
            (trading_account_id, trading_account_id, trading_account_id),
        ).fetchone()
        if not isinstance(row, Mapping):
            raise ExposureInventoryUnavailable("registry/ownership read incomplete")
        accounts = _json_rows(row, "accounts")
        connectors = _json_rows(row, "connectors")
        claims = _json_rows(row, "active_claims")
        owners = _json_rows(row, "open_owners")
        if not account_connector_id:
            # A missing service env var is NOT license to default to a
            # venue-wide connector. Resolve only an exact, uniquely registered
            # connector for this account and the attested mounted credential.
            candidates = [
                c
                for c in connectors
                if (
                    c.get("trading_account_id") == trading_account_id
                    and c.get("status") == "ACTIVE"
                    and c.get("adapter_id") == "BYBIT_V5_PRIVATE"
                    and c.get("credential_ref") == verified_credential_ref
                    and isinstance(verified_credential_ref, str)
                    and verified_credential_ref
                    and isinstance(c.get("account_connector_id"), str)
                    and c["account_connector_id"]
                )
            ]
            if len(candidates) != 1:
                return ExposureAuditResult(
                    None, "IDENTITY_UNKNOWN", ("REGISTERED_CONNECTOR_NOT_UNIQUELY_RESOLVED",)
                )
            account_connector_id = str(candidates[0]["account_connector_id"])
        identity = registered_uid_match(
            snapshot=account_snapshot,
            trading_account_id=trading_account_id,
            account_connector_id=account_connector_id,
            accounts=accounts,
            connectors=connectors,
            verified_credential_ref=verified_credential_ref,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        if not identity.matches:
            return ExposureAuditResult(None, "IDENTITY_UNKNOWN", identity.reasons)
        observations = audit_account_exposure(
            account_snapshot=account_snapshot,
            trading_account_id=trading_account_id,
            active_slot_claims=claims,
            open_owner_rows=owners,
            now=now,
            max_age_seconds=max_age_seconds,
        )
    except (ExposureInventoryUnavailable, ValueError, TypeError, KeyError) as exc:
        return ExposureAuditResult(None, "EVIDENCE_UNKNOWN", (type(exc).__name__,))
    # Proven account; origin remains UNKNOWN. Do not serialize size/Bybit UID
    # into lifecycle fault payloads. Full data stays in transient audit only.
    exposures = tuple(
        {
            "account_identity_binding": "VERIFIED",
            "trading_account_id": observed.trading_account_id,
            "symbol": observed.symbol,
            "position_idx": observed.position_idx,
            "side": observed.side,
            "active_strategy_claim_ids": observed.active_strategy_claim_ids,
            "matching_open_owner_ids": observed.matching_open_owner_ids,
            "open_owner_ids": observed.open_owner_ids,
            "origin": observed.origin,
        }
        for observed in observations
    )
    return ExposureAuditResult(
        exposures,
        "VERIFIED",
        (),
        unknown_open_count=sum(1 for item in exposures if not item["active_strategy_claim_ids"]),
    )


def unbound_strategy_claims(
    exposures: Sequence[Mapping[str, object]], *, account_id: str
) -> tuple[dict[str, object], ...]:
    """No attribution for claim-less positions; independent slot facts only."""
    anomalies: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    for row in exposures:
        if (
            row.get("account_identity_binding") != "VERIFIED"
            or row.get("trading_account_id") != account_id
        ):
            raise ExposureInventoryUnavailable("unproven/cross-account exposure")
        symbol = row.get("symbol")
        idx = row.get("position_idx")
        claim_ids = row.get("active_strategy_claim_ids")
        owner_ids = row.get("matching_open_owner_ids")
        open_owner_ids = row.get("open_owner_ids")
        if (
            not isinstance(symbol, str)
            or not symbol
            or type(idx) is not int
            or idx not in (0, 1, 2)
            or not isinstance(claim_ids, tuple)
            or not isinstance(owner_ids, tuple)
            or not isinstance(open_owner_ids, tuple)
            or any(
                not isinstance(x, str) or not x for x in (*claim_ids, *owner_ids, *open_owner_ids)
            )
        ):
            raise ExposureInventoryUnavailable("invalid exposure fault input")
        key = (symbol, idx)
        if key in seen:
            raise ExposureInventoryUnavailable("duplicate account exposure")
        seen.add(key)
        # Unclaimed Exchange positions may be legitimate control/recovered
        # trades, but two independent OPEN owners violate single-owner.
        if not claim_ids and len(open_owner_ids) < 2:
            continue
        # The one matched bound owner must also be the ONLY OPEN owner.
        if (
            len(claim_ids) == 1
            and len(owner_ids) == 1
            and len(open_owner_ids) == 1
            and owner_ids[0] == open_owner_ids[0]
        ):
            continue
        anomalies.append(
            {
                "trading_account_id": account_id,
                "symbol": symbol,
                "position_idx": idx,
                "exchange_position_slot_claim_ids": claim_ids,
                "matching_open_owner_ids": owner_ids,
                "open_owner_ids": open_owner_ids,
            }
        )
    return tuple(anomalies)
