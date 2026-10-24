"""Provider response parsing + existing registration gate, with fake values only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.account_uid_registration_gate import registered_uid_match
from bybit_workbench.exchange.bybit.account_identity_snapshot import (
    normalize_authenticated_api_key_info,
)

NOW = datetime(2026, 10, 24, tzinfo=UTC)
MAIN_API = {
    "retCode": 0,
    "result": {
        "userID": 123456,
        "isMaster": True,
        "parentUid": "0",
        "uta": 1,
        "apiKey": "DO-NOT-COPY-THIS-FAKE-KEY",
        "secret": "DO-NOT-COPY",
        "permissions": {"ContractTrade": ["Order"]},
        "ips": ["127.0.0.1"],
        "kycLevel": "LEVEL_1",
    },
}
MAIN_ACCOUNT = {
    "trading_account_id": "ta-main",
    "venue_id": "venue-bybit",
    "environment": "MAINNET",
    "status": "ACTIVE",
    "account_kind": "MAIN",
    "account_type": "UNIFIED",
    "external_account_ref": "123456",
    "parent_trading_account_id": None,
}
CONNECTOR = {
    "account_connector_id": "ac-main",
    "trading_account_id": "ta-main",
    "status": "ACTIVE",
    "adapter_id": "BYBIT_V5_PRIVATE",
    "credential_ref": "dummy-mounted-credential",
}


def parse(payload=None, *, proved=True, now=NOW):
    return normalize_authenticated_api_key_info(
        MAIN_API if payload is None else payload,
        observed_at=now,
        signed_get_proven=proved,
    )


def test_main_snapshot_is_minimal_and_normalized() -> None:
    snap = parse()
    assert snap == {
        "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
        "complete": True,
        "observed_at": NOW.isoformat(),
        "account_user_id": "123456",
        "is_master": True,
        "parent_uid": "0",
        "uta": 1,
    }
    assert all(secret not in str(snap) for secret in ("DO-NOT-COPY", "127.0.0.1", "LEVEL_1"))


def test_registered_account_matches_only_with_separately_verified_credential() -> None:
    kwargs = dict(
        snapshot=parse(),
        trading_account_id="ta-main",
        account_connector_id="ac-main",
        accounts=[MAIN_ACCOUNT],
        connectors=[CONNECTOR],
        now=NOW,
    )
    assert registered_uid_match(
        **kwargs, verified_credential_ref="dummy-mounted-credential"
    ).matches
    assert "VERIFIED_CONNECTOR_CREDENTIAL_PROVENANCE_MISSING" in registered_uid_match(
        **kwargs, verified_credential_ref=None
    ).reasons


def test_missing_registered_uid_still_blocks_matching() -> None:
    result = registered_uid_match(
        snapshot=parse(),
        trading_account_id="ta-main",
        account_connector_id="ac-main",
        accounts=[{**MAIN_ACCOUNT, "external_account_ref": None}],
        connectors=[CONNECTOR],
        now=NOW,
        verified_credential_ref="dummy-mounted-credential",
    )
    assert result.matches is False
    assert "REGISTERED_BYBIT_UID_MISSING" in result.reasons


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (None, "BYBIT_API_KEY_INFO_UNAVAILABLE"),
        ({**MAIN_API, "retCode": 10005}, "BYBIT_API_KEY_INFO_REJECTED"),
        ({**MAIN_API, "retCode": True}, "BYBIT_API_KEY_INFO_REJECTED"),
        ({"retCode": 0, "result": []}, "BYBIT_API_KEY_INFO_RESULT_MISSING"),
        (
            {**MAIN_API, "result": {**MAIN_API["result"], "userID": True}},
            "BYBIT_API_KEY_INFO_IDENTITY_INVALID",
        ),
        (
            {**MAIN_API, "result": {**MAIN_API["result"], "userID": "123.4"}},
            "BYBIT_API_KEY_INFO_IDENTITY_INVALID",
        ),
        (
            {**MAIN_API, "result": {**MAIN_API["result"], "parentUid": "-1"}},
            "BYBIT_API_KEY_INFO_IDENTITY_INVALID",
        ),
        (
            {**MAIN_API, "result": {**MAIN_API["result"], "isMaster": "true"}},
            "BYBIT_API_KEY_INFO_IDENTITY_INVALID",
        ),
        (
            {**MAIN_API, "result": {**MAIN_API["result"], "uta": True}},
            "BYBIT_API_KEY_INFO_NOT_UNIFIED",
        ),
        (
            {**MAIN_API, "result": {**MAIN_API["result"], "uta": 0}},
            "BYBIT_API_KEY_INFO_NOT_UNIFIED",
        ),
        (
            {**MAIN_API, "result": {**MAIN_API["result"], "isMaster": False}},
            "BYBIT_API_KEY_INFO_MAIN_SUB_MISMATCH",
        ),
    ],
)
def test_invalid_exchange_reply_fails_closed(payload, reason) -> None:
    with pytest.raises(ValueError, match=reason):
        parse(payload)


def test_missing_proof_and_naive_time_fail_closed() -> None:
    with pytest.raises(ValueError, match="BYBIT_SIGNED_ACCOUNT_GET_NOT_PROVEN"):
        parse(proved=False)
    with pytest.raises(ValueError, match="ACCOUNT_OBSERVATION_TIME_NOT_AWARE"):
        parse(now=datetime(2026, 10, 24))


def test_sub_snapshot_and_parent_uid_are_preserved() -> None:
    payload = {
        **MAIN_API,
        "result": {**MAIN_API["result"], "userID": "654321", "isMaster": False,
                   "parentUid": 123456},
    }
    sub = parse(payload)
    assert sub["account_user_id"] == "654321"
    assert sub["parent_uid"] == "123456"
    assert sub["is_master"] is False
    subaccount = {**MAIN_ACCOUNT, "trading_account_id": "ta-sub",
                  "account_kind": "SUB", "external_account_ref": "654321",
                  "parent_trading_account_id": "ta-main"}
    subconnector = {**CONNECTOR, "account_connector_id": "ac-sub",
                    "trading_account_id": "ta-sub"}
    assert registered_uid_match(
        snapshot=sub, trading_account_id="ta-sub", account_connector_id="ac-sub",
        accounts=[MAIN_ACCOUNT, subaccount], connectors=[subconnector],
        now=NOW, verified_credential_ref="dummy-mounted-credential",
    ).matches


def test_stale_valid_exchange_snapshot_rejected_by_matching_gate() -> None:
    snapshot = parse(now=NOW - timedelta(seconds=30))
    result = registered_uid_match(
        snapshot=snapshot, trading_account_id="ta-main",
        account_connector_id="ac-main", accounts=[MAIN_ACCOUNT],
        connectors=[CONNECTOR], now=NOW,
        verified_credential_ref="dummy-mounted-credential",
    )
    assert "ACCOUNT_SNAPSHOT_INCOMPLETE_OR_STALE" in result.reasons
