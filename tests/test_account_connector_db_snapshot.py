from bybit_workbench.account_connector_db_snapshot import (
    read_account_connector_evidence,
)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class DB:
    def __init__(self, accounts, connectors):
        self.accounts = accounts
        self.connectors = connectors
        self.queries = []

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if "FROM identity.trading_accounts" in sql:
            return Rows(self.accounts)
        if "FROM identity.account_connectors" in sql:
            return Rows(self.connectors)
        raise AssertionError(sql)


def test_registered_multi_account_connector_snapshot():
    db = DB(
        [("acct-1", "venue-bybit", "ACTIVE", "MAINNET"),
         ("acct-2", "venue-okx", "ACTIVE", "MAINNET")],
        [("con-1", "acct-1", "bybit", "secret-1", "ACTIVE"),
         ("con-2", "acct-2", "okx", "secret-2", "ACTIVE")],
    )
    result = read_account_connector_evidence(
        db, required_account_ids=("acct-1", "acct-2")
    )
    assert result.ready
    assert len(db.queries) == 2
    assert db.queries[0][1] == (["acct-1", "acct-2"],)
    assert all("ANY(%s)" in query for query, _ in db.queries)


def test_missing_registration_is_blocked():
    result = read_account_connector_evidence(
        DB([], []), required_account_ids=("acct-1",)
    )
    assert not result.ready
    assert "ACCOUNT_IDENTITY_NOT_REGISTERED" in result.reasons


def test_missing_connector_is_blocked():
    result = read_account_connector_evidence(
        DB([("acct-1", "venue-bybit", "ACTIVE", "MAINNET")], []),
        required_account_ids=("acct-1",),
    )
    assert "ACTIVE_ACCOUNT_CONNECTOR_MISSING" in result.reasons


def test_missing_required_account_identity_never_queries_database():
    db = DB([], [])
    result = read_account_connector_evidence(db, required_account_ids=("",))
    assert not result.ready
    assert db.queries == []


def test_invalid_connector_row_shape_fails_closed():
    import pytest

    db = DB(
        [("acct-1", "venue-bybit", "ACTIVE", "MAINNET")],
        [("con-1", "acct-1")],
    )
    with pytest.raises(ValueError):
        read_account_connector_evidence(db, required_account_ids=("acct-1",))
