"""Read-only, fail-closed per-account connector registration evidence.

This validates registered account and connector identities only. LIVE readiness
still requires current authenticated Exchange facts, execution capabilities,
exact release and explicit OWNER ARM; registration never proves those facts.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AccountConnectorEvidence:
    ready: bool
    reasons: tuple[str, ...]
    account_connectors: Mapping[str, tuple[str, ...]]


def check_account_connector_evidence(
    *, required_account_ids: Iterable[str],
    accounts: Iterable[Mapping[str, Any]],
    connectors: Iterable[Mapping[str, Any]],
) -> AccountConnectorEvidence:
    required = {str(value or "").strip() for value in required_account_ids}
    reasons: set[str] = set()
    if not required or "" in required:
        reasons.add("REQUIRED_ACCOUNT_IDENTITY_MISSING")
    matched: dict[str, Mapping[str, Any]] = {}
    for row in accounts:
        account_id = str(row.get("trading_account_id") or "").strip()
        if account_id not in required:
            continue
        if account_id in matched:
            reasons.add("ACCOUNT_IDENTITY_DUPLICATE")
        matched[account_id] = row
        if str(row.get("status") or "") != "ACTIVE":
            reasons.add("ACCOUNT_NOT_ACTIVE")
        if str(row.get("environment") or "") != "MAINNET":
            reasons.add("ACCOUNT_ENVIRONMENT_NOT_MAINNET")
        if not str(row.get("venue_id") or "").strip():
            reasons.add("ACCOUNT_VENUE_MISSING")
    if any(account_id not in matched for account_id in required if account_id):
        reasons.add("ACCOUNT_IDENTITY_NOT_REGISTERED")

    usable: dict[str, set[str]] = {key: set() for key in required if key}
    ids: set[str] = set()
    for row in connectors:
        account_id = str(row.get("trading_account_id") or "").strip()
        if account_id not in required:
            continue
        connector_id = str(row.get("account_connector_id") or "").strip()
        if not connector_id:
            reasons.add("CONNECTOR_IDENTITY_MISSING")
            continue
        if connector_id in ids:
            reasons.add("CONNECTOR_IDENTITY_DUPLICATE")
        ids.add(connector_id)
        if str(row.get("status") or "") != "ACTIVE":
            continue
        if not str(row.get("adapter_id") or "").strip():
            reasons.add("CONNECTOR_ADAPTER_MISSING")
            continue
        if not str(row.get("credential_ref") or "").strip():
            reasons.add("CONNECTOR_CREDENTIAL_REFERENCE_MISSING")
            continue
        usable[account_id].add(connector_id)
    if any(not usable.get(account_id) for account_id in required if account_id):
        reasons.add("ACTIVE_ACCOUNT_CONNECTOR_MISSING")
    return AccountConnectorEvidence(
        ready=not reasons,
        reasons=tuple(sorted(reasons)),
        account_connectors={key: tuple(sorted(value)) for key, value in sorted(usable.items())},
    )
