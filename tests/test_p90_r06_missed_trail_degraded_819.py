from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


def _arrange_closed_cycle(monkeypatch, owner):
    fill = datetime(2026, 11, 17, 5, 1, tzinfo=UTC)
    boundary = datetime(2026, 11, 17, 5, 5, tzinfo=UTC)
    position = SimpleNamespace(
        fill_at=fill,
        symbol="ARBUSDT",
        strategy_position_id="POS-test",
    )
    scope = SimpleNamespace(identity=SimpleNamespace(origin_commit="a" * 40))
    snapshot = SimpleNamespace(
        closed_at=boundary,
        events=(),
        truncated=False,
    )
    monkeypatch.setattr(owner, "_runtime_scope", lambda *_a, **_k: scope)
    monkeypatch.setattr(owner, "_open_positions", lambda *_a, **_k: (position,))
    monkeypatch.setattr(owner, "_refresh_exit_claim", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_latest_mfe", lambda *_a, **_k: object())
    monkeypatch.setattr(owner, "_last_closed_boundary", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_source_snapshot", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(owner, "_append_mfe", lambda *_a, **_k: 0)
    return boundary


def test_exact_missed_trail_denial_advances_only_degraded_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.connectivity import p90_r06_exit_owner as owner
    from bybit_workbench.universal_exit.p90l90_real_closed_geometry import (
        P90RealClosedGeometryUnverified,
    )

    boundary = _arrange_closed_cycle(monkeypatch, owner)
    markers: list[datetime] = []
    monkeypatch.setattr(
        owner,
        "_process_closed",
        lambda *_a, **_k: (_ for _ in ()).throw(
            P90RealClosedGeometryUnverified(
                "P90_REAL_CLOSED_5M_EXIT_POLICY_DENIED:"
                "P90_REAL_CLOSED_5M_BYBIT_TRAILING_NOT_ARMED"
            )
        ),
    )
    monkeypatch.setattr(
        owner,
        "_record_degraded_closed_boundary",
        lambda *_a, boundary, **_k: markers.append(boundary),
    )

    result = owner.run_p90_r06_exit_once(
        object(), object(), loaded_commit="b" * 40, now=boundary,
    )
    assert markers == [boundary]
    assert result.endswith(
        "ARBUSDT:P90_R06_EXIT_CLOSED_DEGRADED_PROTECTION_ONLY"
    )


def test_other_closed_policy_denial_remains_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.connectivity import p90_r06_exit_owner as owner
    from bybit_workbench.universal_exit.p90l90_real_closed_geometry import (
        P90RealClosedGeometryUnverified,
    )

    boundary = _arrange_closed_cycle(monkeypatch, owner)
    monkeypatch.setattr(
        owner,
        "_process_closed",
        lambda *_a, **_k: (_ for _ in ()).throw(
            P90RealClosedGeometryUnverified(
                "P90_REAL_CLOSED_5M_EXIT_POLICY_DENIED:"
                "P90_REAL_EXIT_ACCOUNT_OR_EXCHANGE_UNPROVEN"
            )
        ),
    )
    monkeypatch.setattr(
        owner,
        "_record_degraded_closed_boundary",
        lambda *_a, **_k: pytest.fail("non-trailing denial must not advance"),
        raising=False,
    )

    with pytest.raises(
        P90RealClosedGeometryUnverified,
        match="P90_REAL_EXIT_ACCOUNT_OR_EXCHANGE_UNPROVEN",
    ):
        owner.run_p90_r06_exit_once(
            object(), object(), loaded_commit="b" * 40, now=boundary,
        )


def test_degraded_boundary_is_part_of_durable_closed_watermark() -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    boundary = datetime(2026, 11, 17, 5, 5, tzinfo=UTC)
    seen: list[tuple[str, tuple[object, ...]]] = []

    class DB:
        def execute(self, sql, params=()):
            seen.append((sql, tuple(params)))
            return SimpleNamespace(fetchone=lambda: {"closed_at": boundary})

    position = SimpleNamespace(strategy_position_id="POS-test")
    assert owner._last_closed_boundary(DB(), position) == boundary
    assert len(seen) == 1
    sql, params = seen[0]
    assert "strategy_exit.exit_observations" in sql
    assert owner._DEGRADED_CLOSED_KIND in params


def test_degraded_marker_is_observation_only_no_execution_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from contextlib import nullcontext
    from operations.connectivity import p90_r06_exit_owner as owner

    boundary = datetime(2026, 11, 17, 5, 5, tzinfo=UTC)
    writes: list[tuple[str, tuple[object, ...]]] = []

    class DB:
        def transaction(self):
            return nullcontext()
        def execute(self, sql, params=()):
            writes.append((sql, tuple(params)))
            return SimpleNamespace()

    connection = DB()
    scope = SimpleNamespace()
    position = SimpleNamespace(
        strategy_position_id="POS-test",
        exit_plan_fingerprint="exit-fp",
        symbol="ARBUSDT",
    )
    snapshot = SimpleNamespace(pin=SimpleNamespace(
        publication_id=123,
        source_session_id="a" * 32,
    ))
    monkeypatch.setattr(owner, "_lock_source", lambda *_a, **_k: None)

    owner._record_degraded_closed_boundary(
        connection,
        scope=scope,
        position=position,
        snapshot=snapshot,
        boundary=boundary,
    )

    assert len(writes) == 1
    sql, params = writes[0]
    assert "INSERT INTO strategy_exit.exit_observations" in sql
    assert "exit_decisions" not in sql
    assert "execution_requests" not in sql
    assert "trade_commands" not in sql
    assert owner._DEGRADED_CLOSED_KIND in params
    attrs = json.loads(str(params[-2]))
    assert attrs["degraded_protection_only"] is True
    assert attrs["exchange_mutation"] is False
    assert attrs["existing_exchange_protection_preserved"] is True


def test_consumer_reports_degraded_before_firstfill_block() -> None:
    from operations.connectivity import universal_exit_consumer as consumer

    result = (
        "P90_R06_FIRSTFILL_OWNER_BLOCKED|FIRSTFILL="
        "P90_REAL_EXIT_FIRST_FILL_ORIGINAL_SOURCE_WINDOW_EXPIRED|CONTINUOUS="
        "P90_R06_EXIT_OWNER_RUNNING|"
        "ARBUSDT:P90_R06_EXIT_CLOSED_DEGRADED_PROTECTION_ONLY"
    )
    state, error = consumer._p90_result_status(result)
    assert state == "P90_R06_EXIT_OWNER_DEGRADED"
    assert error == "P90_R06_EXIT_DEGRADED_PROTECTION_ONLY"


def test_degraded_status_cannot_be_ready_flat() -> None:
    from operations.connectivity import universal_exit_consumer as consumer

    state, error = consumer._p90_result_status(
        "P90_R06_EXIT_OWNER_RUNNING|"
        "ARBUSDT:P90_R06_EXIT_CLOSED_DEGRADED_PROTECTION_ONLY"
    )
    assert state != "P90_R06_EXIT_OWNER_READY_FLAT"
    assert error is not None