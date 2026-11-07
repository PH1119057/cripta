"""No-order regressions for first-fill trailing dispatch before slow reconciliation."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Match the production script directory for its sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operations/connectivity"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research/server/connectivity"))

from operations.connectivity import private_runtime as runtime  # noqa: E402


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
        position_id='position',
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


def test_missing_exit_ownership_sends_no_exchange_command():
    db = DB(child=False)
    with (
        patch.object(runtime, 'TRADING_ACCOUNT_ID', 'ta-test'),
        patch.object(runtime, 'execute_command') as executor,
        pytest.raises(runtime.P90RealExitHandoffBlocked),
    ):
        run(db)
    executor.assert_not_called()
    assert db.events[-1] == 'rollback'


@pytest.mark.parametrize('confirmed', [False, True])
def test_confirmed_protected_entry_is_committed_before_exit_work(confirmed):
    db = DB(parent=confirmed)
    def complete():
        runtime._complete_protected_p90_entry(
            db, entry_command_id='entry', position_id='position',
            entry_result={'actualProtection': {'verifiedFromExchange': True}},
        )
    with patch.object(runtime, 'TRADING_ACCOUNT_ID', 'ta-test'):
        if confirmed:
            complete()
            assert db.events[-1] == 'commit'
        else:
            with pytest.raises(runtime.ExchangeMutationBarrier, match='COMPLETION_DRIFT'):
                complete()
            assert db.events[-1] == 'rollback'
    sql, params = db.events[0]
    assert "p.initial_protection_confirmed_at IS NOT NULL" in sql
    assert "ep.exit_plan_fingerprint=p.exit_plan_fingerprint" in sql
    assert "p.trading_account_id=c.trading_account_id" in sql
    assert "c.state='running'" in sql and "SET state='completed'" in sql
    assert params[2:] == ('entry', 'ta-test', 'position')


def test_unconfirmed_fill_protection_never_completes_entry():
    db = DB()
    with pytest.raises(runtime.ExchangeMutationBarrier, match='PROTECTION_UNVERIFIED'):
        runtime._complete_protected_p90_entry(
            db, entry_command_id='entry', position_id='position', entry_result={},
        )
    assert db.events == []


def test_exit_preparation_fault_keeps_private_execution_alive_and_entry_unchanged():
    db = DB()
    error = runtime.P90RealExitHandoffBlocked('position', 'P90_REAL_POSTFILL_INTERNAL_ERROR')
    with (
        patch.object(runtime, 'TRADING_ACCOUNT_ID', 'ta-test'),
        patch.object(runtime, 'disarm_new_entries') as disarm,
        patch.object(runtime, 'handle_exchange_mutation_barrier') as barrier,
        patch.object(runtime.os, '_exit') as terminate,
    ):
        runtime.handle_p90_real_exit_handoff_block(db, 'entry', error)
    disarm.assert_called_once_with(db, runtime.REAL_ENTRY_RUNTIME_NOT_READY_REASON)
    barrier.assert_not_called()
    terminate.assert_not_called()
    sqls = [e[0] for e in db.events if isinstance(e, tuple)]
    assert len(sqls) == 1 and 'INSERT INTO runtime.connection_events' in sqls[0]
    assert all('UPDATE runtime.trade_commands' not in sql for sql in sqls)


def test_exit_child_dispatch_does_not_complete_or_depend_on_entry_command_state():
    db = DB(parent=False)
    with (
        patch.object(runtime, 'TRADING_ACCOUNT_ID', 'ta-test'),
        patch.object(runtime, 'execute_command') as execute,
    ):
        run(db)
    execute.assert_called_once()
    assert len([e for e in db.events if isinstance(e, tuple)]) == 1


def test_actual_entry_handler_finishes_initial_protection_without_exit_decision():
    import inspect
    body = inspect.getsource(runtime.execute_command)
    start = body.index("_complete_protected_p90_entry(")
    assert start < body.index("reconcile(connection, key, secret, \"after_command\")")
    assert "handoff_real_first_protected_fill(" not in body
    assert "_execute_first_fill_trailing(" not in body
    # The independent Exit owner discovers the durable OPEN position; Entry
    # neither evaluates an ExitPlan nor sends a Bybit trailing command.
    consumer = Path("operations/connectivity/universal_exit_consumer.py").read_text(
        encoding="utf-8",
    )
    assert "run_p90_exit_once(connection, now=current)" in consumer
    owner = Path("operations/connectivity/p90_real_independent_exit.py").read_text(
        encoding="utf-8",
    )
    assert "P90RealPostfillExitPort(" in owner
    assert "P90RealExitExecutionPort(" in owner
    assert "read_real_postfill_source_window(" in owner
    # Existing private worker remains the only Exchange mutation executor.
    worker = inspect.getsource(runtime.command_worker_loop)
    assert worker.index("except P90RealExitHandoffBlocked") < worker.index(
        "except ExchangeMutationBarrier", worker.index("execute_command(connection")
    )



def test_exit_incident_logging_failure_cannot_leave_new_entries_enabled():
    db = DB()
    order = []
    with (
        patch.object(runtime, 'disarm_new_entries',
                     side_effect=lambda *a: order.append('gate-off')),
        patch.object(runtime, 'connection_event', side_effect=RuntimeError('audit unavailable')),
        pytest.raises(RuntimeError, match='audit unavailable'),
    ):
        runtime.handle_p90_real_exit_handoff_block(
            db, 'entry', runtime.P90RealExitHandoffBlocked('position', 'BLOCKED'),
        )
    assert order == ['gate-off']


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

