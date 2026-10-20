from datetime import UTC, datetime

import pytest

from bybit_workbench.entry_account_fanout_db import read_entry_account_fanout

NOW = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class DB:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return Result(self.rows)


def test_multiple_active_accounts_select_deterministically():
    db = DB([
        ("b2", "act", "LEG_B", "ta-2", "DEDICATED_STRATEGY"),
        ("b1", "act", "LEG_A", "ta-1", "DEDICATED_STRATEGY"),
    ])
    result = read_entry_account_fanout(
        db, strategy_activation_id="act",
        authorized_binding_ids=frozenset({"b1", "b2"}), now=NOW,
    )
    assert result.ready_for_account_admission
    assert [r.execution_leg for r in result.legs] == ["LEG_A", "LEG_B"]
    assert db.params == ("act", NOW, NOW)
    assert "effective_from<=%s" in db.sql
    assert "effective_to>%s" in db.sql
    assert "allocation_mode" in db.sql


def test_missing_binding_blocks():
    result = read_entry_account_fanout(
        DB([]), strategy_activation_id="act",
        authorized_binding_ids=frozenset(), now=NOW,
    )
    assert not result.ready_for_account_admission
    assert "EXECUTION_BINDINGS_MISSING" in result.reasons


def test_unauthorized_leg_blocks_every_leg():
    db = DB([
        ("b1", "act", "LEG_A", "ta-1", "DEDICATED_STRATEGY"),
        ("b2", "act", "LEG_B", "ta-2", "DEDICATED_STRATEGY"),
    ])
    result = read_entry_account_fanout(
        db, strategy_activation_id="act",
        authorized_binding_ids=frozenset({"b1"}), now=NOW,
    )
    assert not result.legs
    assert "BINDING_NOT_AUTHORIZED" in result.reasons


def test_duplicate_account_blocks():
    db = DB([
        ("b1", "act", "LEG_A", "ta-1", "DEDICATED_STRATEGY"),
        ("b2", "act", "LEG_B", "ta-1", "DEDICATED_STRATEGY"),
    ])
    result = read_entry_account_fanout(
        db, strategy_activation_id="act",
        authorized_binding_ids=frozenset({"b1", "b2"}), now=NOW,
    )
    assert "ACCOUNT_BOUND_TWICE" in result.reasons


def test_missing_activation_avoids_query():
    db = DB([])
    result = read_entry_account_fanout(
        db, strategy_activation_id="",
        authorized_binding_ids=frozenset(), now=NOW,
    )
    assert not result.ready_for_account_admission
    assert db.sql is None


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        read_entry_account_fanout(
            DB([]), strategy_activation_id="act",
            authorized_binding_ids=frozenset(), now=datetime(2026, 10, 20),
        )


def test_active_non_dedicated_leg_is_not_silently_filtered():
    db = DB([("b1", "act", "LEG_A", "ta-1", "SHARED")])
    result = read_entry_account_fanout(
        db, strategy_activation_id="act",
        authorized_binding_ids=frozenset({"b1"}), now=NOW,
    )
    assert not result.legs
    assert "BINDING_NOT_DEDICATED" in result.reasons


def test_authorized_binding_missing_from_effective_snapshot_blocks():
    db = DB([("b1", "act", "LEG_A", "ta-1", "DEDICATED_STRATEGY")])
    result = read_entry_account_fanout(
        db, strategy_activation_id="act",
        authorized_binding_ids=frozenset({"b1", "b2"}), now=NOW,
    )
    assert not result.legs
    assert "AUTHORIZED_BINDING_SET_NOT_EXACT" in result.reasons
