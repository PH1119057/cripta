from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from bybit_workbench.account_uid_registration_gate import registered_uid_match

NOW = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
MAIN = {
    "trading_account_id": "ta-a",
    "venue_id": "venue-bybit",
    "environment": "MAINNET",
    "status": "ACTIVE",
    "account_kind": "MAIN",
    "account_type": "UNIFIED",
    "external_account_ref": "456789",
    "parent_trading_account_id": None,
}
CONNECTOR = {
    "account_connector_id": "ac-a",
    "trading_account_id": "ta-a",
    "status": "ACTIVE",
    "adapter_id": "BYBIT_V5_PRIVATE",
    "credential_ref": "systemd-credential:bybit-mainnet",
}
SNAPSHOT = {
    "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
    "complete": True,
    "observed_at": NOW.isoformat(),
    "account_user_id": "456789",
    "is_master": True,
    "parent_uid": "0",
    "uta": 1,
}


def check(
    *,
    snapshot=None,
    accounts=None,
    connectors=None,
    credential_ref="systemd-credential:bybit-mainnet",
):
    return registered_uid_match(
        snapshot=SNAPSHOT if snapshot is None else snapshot,
        trading_account_id="ta-a",
        account_connector_id="ac-a",
        accounts=[MAIN] if accounts is None else accounts,
        connectors=[CONNECTOR] if connectors is None else connectors,
        now=NOW,
        verified_credential_ref=credential_ref,
    )


def test_exact_registered_main_account_matches() -> None:
    assert check().matches


def test_missing_registered_provider_uid_blocks() -> None:
    row = {**MAIN, "external_account_ref": None}
    assert "REGISTERED_BYBIT_UID_MISSING" in check(accounts=[row]).reasons


def test_different_exchange_uid_blocks() -> None:
    assert (
        "REGISTERED_BYBIT_UID_MISMATCH"
        in check(snapshot={**SNAPSHOT, "account_user_id": "999"}).reasons
    )


def test_duplicate_active_account_uid_blocks() -> None:
    other = {**MAIN, "trading_account_id": "ta-b"}
    assert "PROVIDER_UID_DUPLICATED_IN_ACTIVE_REGISTRY" in check(accounts=[MAIN, other]).reasons


def test_master_with_sub_provenance_blocks() -> None:
    assert (
        "MAIN_ACCOUNT_TYPE_MISMATCH"
        in check(snapshot={**SNAPSHOT, "is_master": False, "parent_uid": "456"}).reasons
    )


def test_sub_with_exact_parent_matches() -> None:
    sub = {
        **MAIN,
        "account_kind": "SUB",
        "external_account_ref": "789",
        "parent_trading_account_id": "ta-parent",
    }
    parent = {**MAIN, "trading_account_id": "ta-parent", "external_account_ref": "456"}
    response = {**SNAPSHOT, "account_user_id": "789", "is_master": False, "parent_uid": "456"}
    assert registered_uid_match(
        snapshot=response,
        trading_account_id="ta-a",
        account_connector_id="ac-a",
        accounts=[sub, parent],
        connectors=[CONNECTOR],
        now=NOW,
        verified_credential_ref="systemd-credential:bybit-mainnet",
    ).matches


def test_sub_without_proven_parent_blocks() -> None:
    sub = {
        **MAIN,
        "account_kind": "SUB",
        "external_account_ref": "789",
        "parent_trading_account_id": "ta-parent",
    }
    resp = {**SNAPSHOT, "account_user_id": "789", "is_master": False, "parent_uid": "456"}
    result = registered_uid_match(
        snapshot=resp,
        trading_account_id="ta-a",
        account_connector_id="ac-a",
        accounts=[sub],
        connectors=[CONNECTOR],
        now=NOW,
        verified_credential_ref="systemd-credential:bybit-mainnet",
    )
    assert "SUB_ACCOUNT_PARENT_UNPROVEN" in result.reasons


def test_other_connector_account_blocks() -> None:
    assert (
        "CONNECTOR_NOT_REGISTERED_TO_ACCOUNT"
        in check(connectors=[{**CONNECTOR, "trading_account_id": "ta-b"}]).reasons
    )


def test_credential_reference_mismatch_blocks() -> None:
    assert (
        "VERIFIED_CONNECTOR_CREDENTIAL_PROVENANCE_MISSING" in check(credential_ref="other").reasons
    )


def test_no_verified_connector_provenance_blocks() -> None:
    assert "VERIFIED_CONNECTOR_CREDENTIAL_PROVENANCE_MISSING" in check(credential_ref=None).reasons


def test_missing_account_type_blocks() -> None:
    assert (
        "BYBIT_MAIN_SUB_PROVENANCE_MISSING"
        in check(snapshot={**SNAPSHOT, "is_master": None}).reasons
    )


def test_wrong_account_mode_blocks() -> None:
    assert "BYBIT_UNIFIED_ACCOUNT_UNPROVEN" in check(snapshot={**SNAPSHOT, "uta": 0}).reasons


def test_empty_uid_or_false_value_blocks() -> None:
    for u in (None, "", 0, False, "nan", "0", "-1", "1.2"):
        assert not check(snapshot={**SNAPSHOT, "account_user_id": u}).matches


def test_stale_exchange_snapshot_blocks() -> None:
    assert (
        "ACCOUNT_SNAPSHOT_INCOMPLETE_OR_STALE"
        in check(
            snapshot={**SNAPSHOT, "observed_at": (NOW - timedelta(seconds=16)).isoformat()}
        ).reasons
    )


def test_unknown_source_blocks() -> None:
    assert (
        "AUTHENTICATED_EXCHANGE_EVIDENCE_MISSING"
        in check(snapshot={**SNAPSHOT, "source": "LOCAL_DB"}).reasons
    )


def test_duplicate_account_row_blocks() -> None:
    assert "TRADING_ACCOUNT_NOT_UNIQUE" in check(accounts=[MAIN, deepcopy(MAIN)]).reasons


def test_wrong_venue_blocks() -> None:
    assert (
        "TRADING_ACCOUNT_NOT_ACTIVE_BYBIT_MAINNET"
        in check(accounts=[{**MAIN, "venue_id": "other"}]).reasons
    )


def test_wrong_account_key_kind_blocks() -> None:
    assert (
        "REGISTERED_ACCOUNT_KIND_UNSUPPORTED"
        in check(accounts=[{**MAIN, "account_kind": "UNKNOWN"}]).reasons
    )


def test_future_exchange_snapshot_blocks() -> None:
    future = (NOW + timedelta(seconds=2)).isoformat()
    assert (
        "ACCOUNT_SNAPSHOT_INCOMPLETE_OR_STALE"
        in check(snapshot={**SNAPSHOT, "observed_at": future}).reasons
    )


def test_duplicate_connector_id_blocks() -> None:
    assert (
        "ACCOUNT_CONNECTOR_NOT_UNIQUE" in check(connectors=[CONNECTOR, deepcopy(CONNECTOR)]).reasons
    )


def test_parent_from_different_venue_blocks_sub() -> None:
    sub = {
        **MAIN,
        "account_kind": "SUB",
        "external_account_ref": "789",
        "parent_trading_account_id": "ta-parent",
    }
    parent = {
        **MAIN,
        "trading_account_id": "ta-parent",
        "external_account_ref": "456",
        "venue_id": "venue-other",
    }
    response = {**SNAPSHOT, "account_user_id": "789", "is_master": False, "parent_uid": "456"}
    assert (
        "SUB_ACCOUNT_PARENT_MISMATCH"
        in registered_uid_match(
            snapshot=response,
            trading_account_id="ta-a",
            account_connector_id="ac-a",
            accounts=[sub, parent],
            connectors=[CONNECTOR],
            now=NOW,
            verified_credential_ref="systemd-credential:bybit-mainnet",
        ).reasons
    )
