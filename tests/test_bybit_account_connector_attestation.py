from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bybit_workbench.exchange.bybit.account_connector_attestation import (
    attest_bybit_account_connector,
)

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)


class Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class Database:
    def __init__(
        self,
        accounts: list[tuple[object, ...]],
        connectors: list[tuple[object, ...]],
    ) -> None:
        self.accounts = accounts
        self.connectors = connectors
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> Rows:
        self.queries.append((statement, params))
        values = (
            self.accounts
            if "FROM identity.trading_accounts" in statement
            else self.connectors
        )
        return Rows(values)


def main_account() -> tuple[object, ...]:
    return ("ta-main", "venue-bybit", "100", None, "MAIN", "UNIFIED", "MAINNET", "ACTIVE")


def sub_account() -> tuple[object, ...]:
    return ("ta-sub", "venue-bybit", "200", "ta-main", "SUB", "UNIFIED", "MAINNET", "ACTIVE")


def connector(account: str = "ta-main", credential: str = "mounted-main") -> tuple[object, ...]:
    return (
        "conn-main", account, "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE",
        credential, "ACTIVE", None,
    )


def response(user_id: int = 100, parent: str = "0", master: bool = True) -> dict[str, Any]:
    return {
        "retCode": 0,
        "result": {
            "userID": user_id,
            "isMaster": master,
            "parentUid": parent,
            "uta": 1,
            "apiKey": "NEVER_LOG_ME",
            "ips": ["127.0.0.1"],
        },
    }


def check(
    db: Database,
    *,
    get: Any = None,
    account: str = "ta-main",
    credential: str = "mounted-main",
) -> Any:
    return attest_bybit_account_connector(
        db,
        trading_account_id=account,
        account_connector_id="conn-main",
        verified_credential_ref=credential,
        signed_account_get=get or (lambda: response()),
        now=NOW,
    )


def test_main_authenticated_uid_matches_exact_registered_and_mounted_connector() -> None:
    db = Database([main_account()], [connector()])
    outcome = check(db)
    assert outcome.ready and outcome.reasons == ()
    assert len(db.queries) == 2
    assert db.queries[1][1] == ("conn-main",)
    assert all("INSERT " not in sql and "UPDATE " not in sql for sql, _ in db.queries)


def test_sub_uid_and_master_parent_are_both_registered_and_verified() -> None:
    db = Database([main_account(), sub_account()], [connector("ta-sub", "mounted-sub")])
    outcome = check(
        db, account="ta-sub", credential="mounted-sub",
        get=lambda: response(200, "100", False),
    )
    assert outcome.ready and outcome.reasons == ()


def test_independent_sub_attests_without_registered_parent_main() -> None:
    independent = ("ta-sub", "venue-bybit", "200", None, "SUB", "UNIFIED", "MAINNET", "ACTIVE")
    db = Database([independent], [connector("ta-sub", "mounted-sub")])
    outcome = check(
        db, account="ta-sub", credential="mounted-sub",
        get=lambda: response(200, "100", False),
    )
    assert outcome.ready and outcome.reasons == ()


def test_explicit_internal_parent_still_requires_registered_parent() -> None:
    db = Database([sub_account()], [connector("ta-sub", "mounted-sub")])
    outcome = check(
        db, account="ta-sub", credential="mounted-sub",
        get=lambda: response(200, "100", False),
    )
    assert not outcome.ready
    assert "SUB_ACCOUNT_PARENT_UNPROVEN" in outcome.reasons



@pytest.mark.parametrize(
    ("get", "expected"),
    [
        (lambda: response(201), "REGISTERED_BYBIT_UID_MISMATCH"),
        (lambda: response(100, "200", False), "MAIN_ACCOUNT_TYPE_MISMATCH"),
        (lambda: {"retCode": 10005, "result": {}}, "AUTHENTICATED_EXCHANGE_READ_FAILED"),
        (lambda: {"retCode": 0, "result": {"userID": 100}}, "AUTHENTICATED_EXCHANGE_READ_FAILED"),
    ],
)
def test_authentication_or_registered_identity_mismatch_is_blocked(
    get: Any, expected: str
) -> None:
    db = Database([main_account()], [connector()])
    outcome = check(db, get=get)
    assert not outcome.ready
    assert expected in outcome.reasons


def test_missing_mounted_provenance_does_not_perform_signed_request() -> None:
    db = Database([main_account()], [connector()])
    calls = []
    outcome = check(db, credential="", get=lambda: calls.append("sent"))
    assert not outcome.ready
    assert outcome.reasons == ("VERIFIED_CONNECTOR_CREDENTIAL_PROVENANCE_MISSING",)
    assert calls == []
    assert db.queries == []


def test_mismatch_of_mounted_secret_reference_blocks_before_exchange_read() -> None:
    db = Database([main_account()], [connector()])
    calls = []
    outcome = check(db, credential="secret-from-another-account", get=lambda: calls.append("sent"))
    assert not outcome.ready
    assert outcome.reasons == ("ACCOUNT_CONNECTOR_REGISTRATION_OR_MOUNT_MISMATCH",)
    assert calls == []
    assert len(db.queries) == 2


def test_active_sub_parent_mismatch_blocks() -> None:
    db = Database([main_account(), sub_account()], [connector("ta-sub", "mounted-sub")])
    outcome = check(
        db, account="ta-sub", credential="mounted-sub",
        get=lambda: response(200, "999", False),
    )
    assert not outcome.ready
    assert "SUB_ACCOUNT_PARENT_MISMATCH" in outcome.reasons


def test_exchange_exception_cannot_leak_api_key_or_sensitive_payload() -> None:
    db = Database([main_account()], [connector()])
    def broken() -> Any:
        raise ValueError("apikey=top-secret&secret=top-secret")
    outcome = check(db, get=broken)
    assert not outcome.ready
    assert outcome.reasons == ("AUTHENTICATED_EXCHANGE_READ_FAILED",)
    assert "top-secret" not in str(outcome)


def test_duplicate_provider_uid_blocks() -> None:
    duplicate = ("ta-duplicate", "venue-bybit", "100", None, "MAIN", "UNIFIED", "MAINNET", "ACTIVE")
    db = Database([main_account(), duplicate], [connector()])
    outcome = check(db)
    assert not outcome.ready
    assert "PROVIDER_UID_DUPLICATED_IN_ACTIVE_REGISTRY" in outcome.reasons


def test_no_real_credentials_are_required_by_offline_contract() -> None:
    db = Database([main_account()], [connector()])
    observed = []
    result = check(db, get=lambda: observed.append("/v5/user/query-api") or response())
    assert result.ready
    assert observed == ["/v5/user/query-api"]
