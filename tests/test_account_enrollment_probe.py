"""All probe tests are offline: no mounted credentials, network or production DB."""
from __future__ import annotations

from datetime import UTC, datetime

from operations.connectivity.account_enrollment_probe import verify_mounted_account

NOW = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)
CONFIG = {
    "CRIPTA_TRADING_ACCOUNT_ID": "ta-main",
    "CRIPTA_ACCOUNT_CONNECTOR_ID": "ac-main",
    "CRIPTA_EXPECTED_PROVIDER_UID": "654321",
    "CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND": "MAIN",
    "CRIPTA_BYBIT_CREDENTIAL_NAME": "bybit-mainnet",
}
MAIN = ("ta-main", "venue-bybit", None, None, "MAIN", "UNIFIED", "MAINNET", "ACTIVE")
SUB = ("ta-sub", "venue-bybit", "123456", None, "SUB", "UNIFIED", "MAINNET", "ACTIVE")
CONNECTOR = ("ac-main", "ta-main", "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE",
             "mount:main", "ACTIVE", None)
GOOD = {"retCode": 0, "result": {
    "userID": "654321", "isMaster": True, "parentUid": "0", "uta": 1,
    "apiKey": "SECRET_NEVER_ECHO", "ips": ["PRIVATE_IP"],
}}


class Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class Connection:
    def __init__(self, accounts=None, connectors=None):
        self.accounts = [MAIN, SUB] if accounts is None else accounts
        self.connectors = [CONNECTOR] if connectors is None else connectors
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        if "FROM identity.trading_accounts" in sql:
            return Cursor(self.accounts)
        if "FROM identity.account_connectors" in sql:
            return Cursor(self.connectors)
        raise AssertionError("Unexpected SQL: " + sql)


def verify(config=None, response=None, mount=None):
    calls = []
    c = Connection()
    def load(cfg):
        calls.append("mounted")
        return {"api_key": "DUMMY_KEY", "api_secret": "DUMMY_SECRET",
                "credential_ref": "mount:main"} if mount is None else mount
    def get(key, secret):
        calls.append("signed")
        assert (key, secret) == ("DUMMY_KEY", "DUMMY_SECRET")
        return GOOD if response is None else response
    ready, reasons = verify_mounted_account(
        c, config=CONFIG if config is None else config,
        load_mounted=load, signed_get=get, now=NOW,
    )
    return ready, reasons, calls, c.queries


def test_exact_main_uid_authenticated_by_mounted_connector_without_writes():
    ready, reasons, calls, queries = verify()
    assert ready and not reasons
    assert calls == ["mounted", "signed"]
    assert all(sql.strip().startswith("SELECT") for sql in queries)


def test_wrong_uid_is_not_accepted_or_leaked():
    ready, reasons, calls, _ = verify(
        {**CONFIG, "CRIPTA_EXPECTED_PROVIDER_UID": "999999"}
    )
    assert not ready and calls == ["mounted", "signed"]
    assert "PROPOSED_BYBIT_UID_MISMATCH" in reasons
    assert "SECRET_NEVER_ECHO" not in repr(reasons)


def test_wrong_sub_or_key_name_fails_before_secrets_are_loaded():
    for config in (
        {**CONFIG, "CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND": "SUB"},
        {**CONFIG, "CRIPTA_BYBIT_CREDENTIAL_NAME": "bybit-sub-ta-other"},
        {**CONFIG, "CRIPTA_TRADING_ACCOUNT_ID": ""},
    ):
        ready, reasons, calls, queries = verify(config)
        assert not ready and reasons
        assert calls == []
        assert queries == []


def test_wrong_mounted_reference_blocks_before_signed_get():
    ready, reasons, calls, _ = verify(mount={
        "api_key": "DUMMY_KEY", "api_secret": "DUMMY_SECRET",
        "credential_ref": "mount:sub",
    })
    assert not ready and calls == ["mounted"]
    assert reasons == ("ACCOUNT_CONNECTOR_REGISTRATION_OR_MOUNT_MISMATCH",)


def test_signed_sub_response_cannot_register_main():
    ready, reasons, calls, _ = verify(response={"retCode": 0, "result": {
        "userID": "654321", "isMaster": False, "parentUid": "123456", "uta": 1,
    }})
    assert not ready and calls == ["mounted", "signed"]
    assert "MAIN_ACCOUNT_TYPE_MISMATCH" in reasons


def test_invalid_response_fails_closed_with_stable_codes():
    ready, reasons, calls, _ = verify(response={
        "retCode": 10001,
        "result": {"apiKey": "SECRET_NEVER_ECHO"},
    })
    assert not ready and calls == ["mounted", "signed"]
    assert reasons == ("AUTHENTICATED_EXCHANGE_READ_FAILED",)
