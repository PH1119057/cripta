from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.exchange.bybit.account_enrollment_service import (
    persist_verified_account_enrollment,
    verify_account_enrollment,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
MAIN = ("ta-main", "venue-bybit", None, None, "MAIN", "UNIFIED", "MAINNET", "ACTIVE")
SUB = ("ta-sub", "venue-bybit", "123456", None, "SUB", "UNIFIED", "MAINNET", "ACTIVE")
CONNECTOR = ("ac-main", "ta-main", "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE",
             "mounted:main", "ACTIVE", None)
RESPONSE = {
    "retCode": 0,
    "result": {"userID": "654321", "parentUid": "0", "isMaster": True, "uta": 1,
               "apiKey": "NEVER_LOG", "ips": ["secret-ip"]},
}


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, accounts=None, connectors=None, serializable=True):
        self.accounts = [MAIN, SUB] if accounts is None else accounts
        self.connectors = [CONNECTOR] if connectors is None else connectors
        self.serializable = serializable
        self.updated = []

    def execute(self, sql, params=None):
        if "FROM identity.trading_accounts" in sql and sql.lstrip().startswith("SELECT"):
            return Cursor(self.accounts)
        if "FROM identity.account_connectors" in sql and sql.lstrip().startswith("SELECT"):
            return Cursor(self.connectors)
        if sql == "SHOW transaction_isolation":
            return Cursor([("serializable" if self.serializable else "read committed",)])
        if sql.lstrip().startswith("UPDATE identity.trading_accounts"):
            self.updated.append(params)
            return Cursor([("ta-main",)])
        raise AssertionError("unexpected SQL statement")


def check(connection=None, *, proposed_uid="654321", response=None, ref="mounted:main"):
    c = connection or Connection()
    calls = []
    def signed():
        calls.append("signed")
        return RESPONSE if response is None else response
    result = verify_account_enrollment(
        c, trading_account_id="ta-main", account_connector_id="ac-main",
        proposed_provider_uid=proposed_uid, mounted_credential_ref=ref,
        signed_account_get=signed, now=NOW,
    )
    return result, calls


def test_first_main_registration_accepts_fresh_signed_evidence() -> None:
    result, calls = check()
    assert result.ready
    assert calls == ["signed"]
    assert result.evidence is not None
    assert result.evidence.provider_uid == "654321"
    assert "654321" not in repr(result)
    assert "NEVER_LOG" not in repr(result)


def test_candidate_mismatch_never_persists() -> None:
    result, _ = check(proposed_uid="999999")
    assert not result.ready
    assert "PROPOSED_BYBIT_UID_MISMATCH" in result.reasons
    assert result.evidence is None


def test_bad_mount_rejected_before_signed_get() -> None:
    result, calls = check(ref="incorrect-mount")
    assert not result.ready
    assert calls == []
    assert result.reasons == ("ACCOUNT_CONNECTOR_REGISTRATION_OR_MOUNT_MISMATCH",)


def test_existing_different_uid_never_overwritten() -> None:
    wrong = ("ta-main", "venue-bybit", "11111", None, "MAIN", "UNIFIED", "MAINNET", "ACTIVE")
    result, calls = check(Connection(accounts=[wrong, SUB]))
    assert not result.ready
    assert calls == []
    assert "REGISTERED_BYBIT_UID_MISMATCH" in result.reasons


def test_signed_sub_key_cannot_register_main() -> None:
    response = {"retCode": 0, "result": {
        "userID": "654321", "parentUid": "123456",
        "isMaster": False, "uta": 1,
    }}
    result, _ = check(response=response)
    assert not result.ready
    assert "MAIN_ACCOUNT_TYPE_MISMATCH" in result.reasons


def test_signed_get_error_is_redacted() -> None:
    def failing():
        raise RuntimeError("api_secret=DO_NOT_PRINT")
    result = verify_account_enrollment(
        Connection(), trading_account_id="ta-main", account_connector_id="ac-main",
        proposed_provider_uid="654321", mounted_credential_ref="mounted:main",
        signed_account_get=failing, now=NOW,
    )
    assert result.reasons == ("AUTHENTICATED_EXCHANGE_READ_FAILED",)
    assert "DO_NOT_PRINT" not in repr(result)


def test_duplicate_uid_in_another_active_account_blocks() -> None:
    sub = ("ta-sub", "venue-bybit", "654321", None, "SUB", "UNIFIED", "MAINNET", "ACTIVE")
    result, _ = check(Connection(accounts=[MAIN, sub]))
    assert not result.ready
    assert "PROVIDER_UID_DUPLICATED_IN_ACTIVE_REGISTRY" in result.reasons


def test_persist_requires_owner_and_serializable_transaction() -> None:
    result, _ = check()
    assert result.evidence is not None
    c = Connection(serializable=False)
    with pytest.raises(RuntimeError, match="OWNER_APPROVAL_MISSING"):
        persist_verified_account_enrollment(
            c, evidence=result.evidence, now=NOW, approved_operator_ref=""
        )
    with pytest.raises(RuntimeError, match="SERIALIZABLE_REQUIRED"):
        persist_verified_account_enrollment(
            c, evidence=result.evidence, now=NOW, approved_operator_ref="owner-audit"
        )
    assert c.updated == []


def test_persist_uses_exact_conditional_registry_write_without_commit() -> None:
    result, _ = check()
    assert result.evidence is not None
    c = Connection()
    persist_verified_account_enrollment(
        c, evidence=result.evidence, now=NOW, approved_operator_ref="owner-audit"
    )
    assert c.updated == [(
        "654321", "owner-audit", NOW, "ta-main",
        "654321", "ac-main", "mounted:main", "654321",
    )]
    with pytest.raises(RuntimeError, match="ATTESTATION_STALE"):
        persist_verified_account_enrollment(
            c, evidence=result.evidence, now=NOW + timedelta(seconds=16),
            approved_operator_ref="owner-audit",
        )
