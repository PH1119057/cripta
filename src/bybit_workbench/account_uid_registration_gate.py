"""Pure registered Bybit UID versus TradingAccount check (offline candidate).

Not a credential attestation, Exchange cycle proof, trading authorization,
owner decision, DB mutation, or substitute for live connector verification.
Caller must obtain authenticated Bybit evidence and consistent PostgreSQL rows
independently, scoped to one authorized connector.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class RegisteredUidMatch:
    matches: bool
    reasons: tuple[str, ...]


def _uid(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    raw = str(value)
    if not raw.isascii() or not raw.isdecimal() or not raw or int(raw) <= 0:
        return ""
    return str(int(raw))


def _timestamp_valid(snapshot: Mapping[str, object], now: datetime, max_age_seconds: int) -> bool:
    if (
        not isinstance(max_age_seconds, int)
        or isinstance(max_age_seconds, bool)
        or max_age_seconds <= 0
    ):
        return False
    if now.tzinfo is None:
        return False
    try:
        observed = datetime.fromisoformat(str(snapshot["observed_at"]))
    except (ValueError, KeyError, TypeError):
        return False
    if observed.tzinfo is None:
        return False
    delta = (now.astimezone(UTC) - observed.astimezone(UTC)).total_seconds()
    return 0 <= delta <= max_age_seconds


def registered_uid_match(
    *,
    snapshot: Mapping[str, object] | None,
    trading_account_id: str,
    account_connector_id: str,
    accounts: Sequence[Mapping[str, object]],
    connectors: Sequence[Mapping[str, object]],
    now: datetime,
    max_age_seconds: int = 15,
    # Authenticated credential provenance must come from a *separately verified*
    # runtime mount/config, not copied from the DB's credential_ref value.
    verified_credential_ref: str | None = None,
) -> RegisteredUidMatch:
    reasons: set[str] = set()
    if not trading_account_id or not account_connector_id:
        reasons.add("REQUIRED_IDENTITY_MISSING")
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("source") != "BYBIT_AUTHENTICATED_ACCOUNT_GET"
    ):
        reasons.add("AUTHENTICATED_EXCHANGE_EVIDENCE_MISSING")
        snapshot = {}
    if snapshot.get("complete") is not True or not _timestamp_valid(snapshot, now, max_age_seconds):
        reasons.add("ACCOUNT_SNAPSHOT_INCOMPLETE_OR_STALE")
    observed_uid = _uid(snapshot.get("account_user_id"))
    if not observed_uid:
        reasons.add("BYBIT_UID_MISSING_OR_INVALID")
    is_master = snapshot.get("is_master")
    parent_uid = str(snapshot.get("parent_uid") or "").strip()
    if type(is_master) is not bool or not parent_uid.isascii() or not parent_uid.isdecimal():
        reasons.add("BYBIT_MAIN_SUB_PROVENANCE_MISSING")
    if snapshot.get("uta") != 1 or type(snapshot.get("uta")) is bool:
        reasons.add("BYBIT_UNIFIED_ACCOUNT_UNPROVEN")
    matches = [a for a in accounts if a.get("trading_account_id") == trading_account_id]
    if len(matches) != 1:
        reasons.add("TRADING_ACCOUNT_NOT_UNIQUE")
    a = matches[0] if len(matches) == 1 else {}
    if (
        a.get("venue_id") != "venue-bybit"
        or a.get("environment") != "MAINNET"
        or a.get("status") != "ACTIVE"
    ):
        reasons.add("TRADING_ACCOUNT_NOT_ACTIVE_BYBIT_MAINNET")
    registered_uid = _uid(a.get("external_account_ref"))
    if not registered_uid:
        reasons.add("REGISTERED_BYBIT_UID_MISSING")
    if registered_uid and observed_uid and registered_uid != observed_uid:
        reasons.add("REGISTERED_BYBIT_UID_MISMATCH")
    duplicate = [
        row
        for row in accounts
        if row.get("trading_account_id") != trading_account_id
        and row.get("status") == "ACTIVE"
        and row.get("venue_id") == "venue-bybit"
        and row.get("environment") == "MAINNET"
        and registered_uid
        and _uid(row.get("external_account_ref")) == registered_uid
    ]
    if duplicate:
        reasons.add("PROVIDER_UID_DUPLICATED_IN_ACTIVE_REGISTRY")
    kind = a.get("account_kind")
    if kind == "MAIN":
        if (
            is_master is not True
            or parent_uid != "0"
            or a.get("parent_trading_account_id") is not None
        ):
            reasons.add("MAIN_ACCOUNT_TYPE_MISMATCH")
    elif kind == "SUB":
        # The provider's parentUid is mandatory evidence that this key belongs
        # to a SUB, but the parent MAIN need not be onboarded into CRIPTA.
        # A configured internal parent link, if present, must still match the
        # exact registered parent and its authenticated provider provenance.
        parent_id = a.get("parent_trading_account_id")
        if is_master is not False or not parent_uid.isdecimal() or parent_uid == "0":
            reasons.add("SUB_ACCOUNT_PARENT_UNPROVEN")
        elif parent_id is not None:
            parent_rows = [
                p for p in accounts if p.get("trading_account_id") == parent_id
            ]
            if len(parent_rows) != 1:
                reasons.add("SUB_ACCOUNT_PARENT_UNPROVEN")
            elif (
                _uid(parent_rows[0].get("external_account_ref")) != _uid(parent_uid)
                or parent_rows[0].get("account_kind") != "MAIN"
                or parent_rows[0].get("status") != "ACTIVE"
                or parent_rows[0].get("environment") != "MAINNET"
                or parent_rows[0].get("account_type") != "UNIFIED"
                or parent_rows[0].get("venue_id") != a.get("venue_id")
            ):
                reasons.add("SUB_ACCOUNT_PARENT_MISMATCH")
    else:
        reasons.add("REGISTERED_ACCOUNT_KIND_UNSUPPORTED")
    if a.get("account_type") != "UNIFIED":
        reasons.add("REGISTERED_ACCOUNT_TYPE_MISMATCH")
    cmatches = [c for c in connectors if c.get("account_connector_id") == account_connector_id]
    if len(cmatches) != 1:
        reasons.add("ACCOUNT_CONNECTOR_NOT_UNIQUE")
    c = cmatches[0] if len(cmatches) == 1 else {}
    if (
        c.get("trading_account_id") != trading_account_id
        or c.get("status") != "ACTIVE"
        or c.get("adapter_id") != "BYBIT_V5_PRIVATE"
    ):
        reasons.add("CONNECTOR_NOT_REGISTERED_TO_ACCOUNT")
    registered_ref = c.get("credential_ref")
    if (
        not isinstance(verified_credential_ref, str)
        or not verified_credential_ref
        or not isinstance(registered_ref, str)
        or not registered_ref
        or registered_ref != verified_credential_ref
    ):
        reasons.add("VERIFIED_CONNECTOR_CREDENTIAL_PROVENANCE_MISSING")
    return RegisteredUidMatch(matches=not reasons, reasons=tuple(sorted(reasons)))
