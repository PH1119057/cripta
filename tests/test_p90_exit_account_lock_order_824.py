from __future__ import annotations

import inspect
from types import SimpleNamespace


def test_shared_account_lock_uses_exact_account_key() -> None:
    from bybit_workbench.universal_exit import p90l90_real_exit_execution as execution

    seen: list[tuple[str, tuple[object, ...]]] = []

    class DB:
        def execute(self, sql, params=()):
            seen.append((sql, tuple(params)))
            return SimpleNamespace()

    account = "ta-" + "8" * 32
    execution.lock_p90_exit_account(DB(), account)

    assert seen == [
        (
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (account,),
        )
    ]


def test_firstfill_takes_account_lock_before_position_lock_and_row_for_update() -> None:
    from operations.connectivity import p90_real_independent_exit as firstfill

    source = inspect.getsource(firstfill.run_p90_exit_once)
    account_lock = source.index("lock_p90_exit_account(")
    position_lock = source.index(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))"
    )
    row_lock = source.index("FOR UPDATE")
    assert account_lock < position_lock < row_lock


def test_r06_closed_takes_account_lock_before_source_and_exit_persistence() -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    source = inspect.getsource(owner._process_closed)
    account_lock = source.index("lock_p90_exit_account(")
    source_lock = source.index("_lock_source(")
    port_call = source.index("result = port(fact)")
    assert account_lock < source_lock < port_call


def test_execution_port_keeps_inner_account_lock_as_defense_in_depth() -> None:
    from bybit_workbench.universal_exit import p90l90_real_exit_execution as execution

    source = inspect.getsource(execution.P90RealExitExecutionPort.__call__)
    lock = source.index("lock_p90_exit_account(")
    binding = source.index("load_exit_binding(")
    persist = source.index("persist_exit_execution_request(")
    assert lock < binding < persist
