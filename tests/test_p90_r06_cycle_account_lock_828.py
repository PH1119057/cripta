from __future__ import annotations

import inspect


def test_r06_position_cycle_takes_account_lock_before_claim_mfe_and_closed_exit() -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    source = inspect.getsource(owner.run_p90_r06_exit_once)
    loop = source.index("for position in positions:")
    account_scope = source.index(
        "scope.routes.account_by_strategy.get(position.strategy_id)", loop
    )
    account_lock = source.index("lock_p90_exit_account(", loop)
    claim = source.index("_refresh_exit_claim(", loop)
    latest = source.index("_latest_mfe(", loop)
    closed_boundary = source.index("_last_closed_boundary(", loop)
    append_mfe = source.index("_append_mfe(", loop)
    closed_exit = source.index("_process_closed(", loop)

    assert (
        loop
        < account_scope
        < account_lock
        < claim
        < latest
        < closed_boundary
        < append_mfe
        < closed_exit
    )


def test_r06_cycle_validates_route_account_matches_position_before_lock() -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    source = inspect.getsource(owner.run_p90_r06_exit_once)
    loop = source.index("for position in positions:")
    route_check = source.index(
        "account_id == position.trading_account_id", loop
    )
    account_lock = source.index("lock_p90_exit_account(", loop)
    assert route_check < account_lock


def test_inner_closed_and_execution_locks_remain_defense_in_depth() -> None:
    from operations.connectivity import p90_r06_exit_owner as owner
    from bybit_workbench.universal_exit import p90l90_real_exit_execution as execution

    assert "lock_p90_exit_account(" in inspect.getsource(owner._process_closed)
    assert "lock_p90_exit_account(" in inspect.getsource(
        execution.P90RealExitExecutionPort.__call__
    )


def test_r06_runtime_calls_account_lock_before_claim_and_mfe(monkeypatch) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from operations.connectivity import p90_r06_exit_owner as owner

    boundary = datetime(2026, 11, 17, 5, 5, tzinfo=UTC)
    strategy_id = "p90l90_legacy_sub_dot_arb_apt"
    account_id = "ta-" + "8" * 32
    position = SimpleNamespace(
        fill_at=datetime(2026, 11, 17, 5, 1, tzinfo=UTC),
        symbol="ARBUSDT",
        strategy_position_id="p",
        strategy_id=strategy_id,
        trading_account_id=account_id,
    )
    scope = SimpleNamespace(
        identity=SimpleNamespace(origin_commit="a" * 40),
        routes=SimpleNamespace(account_by_strategy={strategy_id: account_id}),
    )
    snapshot = SimpleNamespace(
        closed_at=boundary,
        events=(),
        truncated=False,
    )
    calls: list[str] = []

    monkeypatch.setattr(owner, "_runtime_scope", lambda *_a, **_k: scope)
    monkeypatch.setattr(owner, "_open_positions", lambda *_a, **_k: (position,))
    monkeypatch.setattr(
        owner,
        "lock_p90_exit_account",
        lambda _db, actual: calls.append(f"lock:{actual}"),
    )
    monkeypatch.setattr(
        owner, "_refresh_exit_claim",
        lambda *_a, **_k: calls.append("claim"),
    )
    monkeypatch.setattr(
        owner, "_latest_mfe",
        lambda *_a, **_k: calls.append("latest_mfe") or object(),
    )
    monkeypatch.setattr(
        owner, "_last_closed_boundary",
        lambda *_a, **_k: calls.append("last_closed") or None,
    )
    monkeypatch.setattr(
        owner, "_source_snapshot",
        lambda *_a, **_k: calls.append("snapshot") or snapshot,
    )
    monkeypatch.setattr(
        owner, "_before_boundary",
        lambda *_a, **_k: ((), True),
    )
    monkeypatch.setattr(
        owner, "_append_mfe",
        lambda *_a, **_k: calls.append("append_mfe") or 0,
    )
    monkeypatch.setattr(
        owner, "_process_closed",
        lambda *_a, **_k: calls.append("closed") or "P90_R06_EXIT_CLOSED_NOOP",
    )

    result = owner.run_p90_r06_exit_once(
        object(), object(), loaded_commit="b" * 40, now=boundary,
    )

    assert calls[0] == f"lock:{account_id}"
    assert calls.index("lock:" + account_id) < calls.index("claim")
    assert calls.index("claim") < calls.index("append_mfe")
    assert calls.index("append_mfe") < calls.index("closed")
    assert result.endswith("ARBUSDT:P90_R06_EXIT_CLOSED_NOOP")


def test_r06_route_mismatch_blocks_before_account_lock_or_claim(monkeypatch) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import pytest
    from operations.connectivity import p90_r06_exit_owner as owner

    strategy_id = "p90l90_legacy_sub_dot_arb_apt"
    position = SimpleNamespace(
        fill_at=datetime(2026, 11, 17, 5, 1, tzinfo=UTC),
        symbol="ARBUSDT",
        strategy_position_id="p",
        strategy_id=strategy_id,
        trading_account_id="ta-" + "8" * 32,
    )
    scope = SimpleNamespace(
        identity=SimpleNamespace(origin_commit="a" * 40),
        routes=SimpleNamespace(
            account_by_strategy={strategy_id: "ta-" + "9" * 32}
        ),
    )

    monkeypatch.setattr(owner, "_runtime_scope", lambda *_a, **_k: scope)
    monkeypatch.setattr(owner, "_open_positions", lambda *_a, **_k: (position,))
    monkeypatch.setattr(
        owner,
        "lock_p90_exit_account",
        lambda *_a, **_k: pytest.fail("lock must not run for mismatched route"),
    )
    monkeypatch.setattr(
        owner,
        "_refresh_exit_claim",
        lambda *_a, **_k: pytest.fail("claim must not run for mismatched route"),
    )

    with pytest.raises(
        owner.P90R06ExitBlocked,
        match="P90_R06_EXIT_ACCOUNT_LOCK_SCOPE_UNVERIFIED",
    ):
        owner.run_p90_r06_exit_once(
            object(),
            object(),
            loaded_commit="b" * 40,
            now=datetime(2026, 11, 17, 5, 5, tzinfo=UTC),
        )
