"""No-order regressions for first-fill trailing dispatch before slow reconciliation."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from operations.connectivity import private_runtime as runtime


class DB:
    def __init__(self, *, child=True, parent=True):
        self.events = []
        self.child = child
        self.parent = parent

    def execute(self, sql, params):
        self.events.append((sql, params))
        if 'RETURNING c.command_id' in sql:
            assert "r.action_kind='SET_TRAILING'" in sql
            assert params[1:] == ('decision', 'entry', 'ta-test')
            return SimpleNamespace(fetchone=lambda: (
                ('exit-command', 'strategy_exit', 'LTCUSDT', '{}') if self.child else None
            ))
        return SimpleNamespace(rowcount=int(self.parent))

    def commit(self):
        self.events.append('commit')

    def rollback(self):
        self.events.append('rollback')


def run(db):
    runtime._execute_first_fill_trailing(
        db, 'fake', 'fake', entry_command_id='entry', exit_decision_id='decision',
        entry_result={'actualProtection': {'verifiedFromExchange': True}},
    )


def test_exact_durable_trailing_uses_normal_executor_after_parent_commit():
    db = DB()
    def execute(conn, key, secret, row):
        assert conn is db and db.events[-1] == 'commit'
        assert row[0] == 'exit-command'
        db.events.append('execute-native-trailing')
    with (
        patch.object(runtime, 'TRADING_ACCOUNT_ID', 'ta-test'),
        patch.object(runtime, 'execute_command', side_effect=execute) as executor,
        patch.object(runtime, 'reconcile') as reconcile,
    ):
        run(db)
    executor.assert_called_once()
    reconcile.assert_not_called()
    assert db.events[-1] == 'execute-native-trailing'


@pytest.mark.parametrize('child,parent', [(False, True), (True, False)])
def test_missing_or_racing_ownership_sends_no_exchange_command(child, parent):
    db = DB(child=child, parent=parent)
    with (
        patch.object(runtime, 'TRADING_ACCOUNT_ID', 'ta-test'),
        patch.object(runtime, 'execute_command') as executor,
        pytest.raises(runtime.ExchangeMutationBarrier),
    ):
        run(db)
    executor.assert_not_called()
    assert db.events[-1] == 'rollback'


def test_ambiguous_child_failure_is_attributed_to_exit_not_confirmed_entry():
    db = DB()
    with (
        patch.object(runtime, 'TRADING_ACCOUNT_ID', 'ta-test'),
        patch.object(runtime, 'execute_command',
                     side_effect=runtime.ExchangeMutationBarrier('unknown')),
        patch.object(runtime, 'handle_exchange_mutation_barrier') as barrier,
    ):
        run(db)
    assert barrier.call_args.args[3] == 'exit-command'

