from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_real_readiness_requires_r06_p90_exit_owner_service() -> None:
    from bybit_workbench.real_service_profiles import p90_real_v3_service_specs

    specs = p90_real_v3_service_specs(sub_account_id="ta-" + "1" * 32)
    names = {item.unit for item in specs}
    assert "cripta-universal-exit-consumer.service" in names


def test_exit_unit_is_p90_only_and_keeps_generic_legacy_exit_disabled() -> None:
    unit = (ROOT / "operations/systemd/cripta-universal-exit-consumer.service").read_text()
    assert "CRIPTA_UNIVERSAL_EXIT_MAINNET_CONSUMER=DISABLED" in unit
    assert "CRIPTA_P90_R06_EXIT_OWNER=ENABLED" in unit
    assert "CRIPTA_R09_NATIVE_PUBLICATION_PARITY=REQUIRE" in unit
    assert "StateDirectory=cripta-p90-r06-exit" in unit


def test_prearm_requires_fresh_p90_exit_owner_status() -> None:
    source = (
        ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py"
    ).read_text(encoding="utf-8")
    assert "/var/lib/cripta-p90-r06-exit/status.json" in source
    assert "P90_R06_EXIT_OWNER_READY_FLAT" in source
    assert "p90_r06_exit_owner" in source


@pytest.mark.parametrize(
    ("state", "raw_result", "error", "expected_result", "expected_recovery"),
    [
        (
            "P90_R06_EXIT_OWNER_READY_FLAT",
            "RECOVERY=P90_TRAIL_RECOVERY_NO_ELIGIBLE_POSITION|"
            "P90_R06_EXIT_OWNER_READY_FLAT",
            None,
            "P90_R06_EXIT_OWNER_READY_FLAT",
            "P90_TRAIL_RECOVERY_NO_ELIGIBLE_POSITION",
        ),
        (
            "P90_R06_EXIT_OWNER_READY_FLAT",
            "RECOVERY=P90_TRAIL_RECOVERY_BLOCKED:P90_SIGNED_PROOF_MISSING|"
            "P90_R06_EXIT_OWNER_READY_FLAT",
            None,
            "RECOVERY=P90_TRAIL_RECOVERY_BLOCKED:P90_SIGNED_PROOF_MISSING|"
            "P90_R06_EXIT_OWNER_READY_FLAT",
            None,
        ),
        (
            "P90_R06_EXIT_OWNER_RUNNING",
            "RECOVERY=P90_TRAIL_RECOVERY_NO_ELIGIBLE_POSITION|"
            "P90_R06_EXIT_OWNER_RUNNING",
            None,
            "RECOVERY=P90_TRAIL_RECOVERY_NO_ELIGIBLE_POSITION|"
            "P90_R06_EXIT_OWNER_RUNNING",
            None,
        ),
        (
            "P90_R06_EXIT_OWNER_READY_FLAT",
            "RECOVERY=P90_TRAIL_RECOVERY_NO_ELIGIBLE_POSITION|"
            "P90_R06_EXIT_OWNER_READY_FLAT",
            "P90_R06_EXIT_ERROR",
            "RECOVERY=P90_TRAIL_RECOVERY_NO_ELIGIBLE_POSITION|"
            "P90_R06_EXIT_OWNER_READY_FLAT",
            None,
        ),
    ],
)
def test_r06_exit_published_flat_result_keeps_gateway_exact_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: str,
    raw_result: str,
    error: str | None,
    expected_result: str,
    expected_recovery: str | None,
) -> None:
    # #860 regression: old READY_FLAT producer was made non-exact by
    # a harmless recovery prefix. Gateway itself is intentionally untouched.
    from operations.connectivity import universal_exit_consumer as consumer

    status_path = tmp_path / "p90-r06-exit-status.json"
    monkeypatch.setattr(consumer, "P90_R06_OWNER", "ENABLED")
    monkeypatch.setattr(consumer, "CONSUMER_ARM", "DISABLED")
    monkeypatch.setattr(consumer, "LOADED_COMMIT", "a" * 40)
    monkeypatch.setattr(consumer, "STATUS_PATH", status_path)
    consumer._p90_status(state=state, result=raw_result, error=error)
    status = consumer.json.loads(status_path.read_text(encoding="utf-8"))

    assert status["state"] == state
    assert status["result"] == expected_result
    assert status["recovery_result"] == expected_recovery if expected_recovery else (
        "recovery_result" not in status
    )
    assert status["error"] == error
    assert status["release_commit"] == "a" * 40
    assert status["generic_consumer"] == "DISABLED"
    assert status["p90_r06_exit_owner"] == "ENABLED"



def test_r06_exit_owner_module_has_no_public_exchange_network_client() -> None:
    source = (
        ROOT / "operations/connectivity/p90_r06_exit_owner.py"
    ).read_text(encoding="utf-8")
    assert "urllib" not in source
    assert "requests." not in source
    assert "pybit" not in source
    assert "/v5/market/" not in source
    assert "/v5/order/" not in source
    assert "/v5/position/" not in source


def test_next_closed_boundary_is_strictly_after_fill_and_contiguous() -> None:
    from operations.connectivity.p90_r06_exit_owner import _next_closed_boundary

    fill = datetime(2026, 11, 17, 5, 1, 2, tzinfo=UTC)
    assert _next_closed_boundary(fill, None) == datetime(
        2026, 11, 17, 5, 5, tzinfo=UTC
    )
    exact = datetime(2026, 11, 17, 5, 5, tzinfo=UTC)
    assert _next_closed_boundary(exact, None) == datetime(
        2026, 11, 17, 5, 10, tzinfo=UTC
    )
    assert _next_closed_boundary(fill, datetime(
        2026, 11, 17, 5, 15, tzinfo=UTC
    )) == datetime(2026, 11, 17, 5, 20, tzinfo=UTC)


def test_mfe_anchor_refs_are_exact_and_source_scoped() -> None:
    from operations.connectivity.p90_r06_exit_owner import (
        P90R06ExitBlocked,
        _mfe_source_anchor,
    )

    refs = (
        "bybit:publicTrade:LTCUSDT:exec-1",
        "market-source-session:" + "a" * 32,
        "market-source-frame:123:4",
    )
    assert _mfe_source_anchor(refs) == ("a" * 32, 123, 4)
    with pytest.raises(P90R06ExitBlocked, match="MFE_SOURCE_ANCHOR"):
        _mfe_source_anchor(refs + ("market-source-frame:124:0",))


def test_native_batch_never_crosses_pending_closed_boundary() -> None:
    from operations.connectivity.p90_r06_exit_owner import _before_boundary

    boundary = datetime(2026, 11, 17, 5, 10, tzinfo=UTC)
    events = (
        SimpleNamespace(event_at=boundary - timedelta(microseconds=1)),
        SimpleNamespace(event_at=boundary),
        SimpleNamespace(event_at=boundary + timedelta(seconds=1)),
    )
    selected, complete = _before_boundary(events, boundary=boundary, truncated=False)
    assert selected == events[:1]
    assert complete is True
    selected, complete = _before_boundary(events[:1], boundary=boundary, truncated=True)
    assert selected == events[:1]
    assert complete is False


def test_consumer_runs_p90_owner_when_generic_exit_is_disabled() -> None:
    source = (
        ROOT / "operations/connectivity/universal_exit_consumer.py"
    ).read_text(encoding="utf-8")
    assert '"CRIPTA_P90_R06_EXIT_OWNER", "DISABLED"' in source
    assert "run_p90_r06_exit_once(" in source
    assert 'run_generic=CONSUMER_ARM == "ENABLED"' in source


def _event(at, frame):
    return SimpleNamespace(
        event_at=at, frame_number=frame, frame_index=0,
    )


def test_post_boundary_257th_event_does_not_block_current_closed_boundary() -> None:
    from operations.connectivity.p90_r06_exit_owner import (
        _MAX_BATCH,
        _bounded_published_events,
    )

    boundary = datetime(2026, 11, 17, 5, 10, tzinfo=UTC)
    events = tuple(
        _event(boundary + timedelta(microseconds=i + 1), i + 1)
        for i in range(_MAX_BATCH + 1)
    )
    selected, truncated = _bounded_published_events(
        events, causal_boundary=boundary,
    )
    assert len(selected) == _MAX_BATCH
    assert truncated is False


def test_257th_pre_boundary_event_requires_another_native_catchup_cycle() -> None:
    from operations.connectivity.p90_r06_exit_owner import (
        _MAX_BATCH,
        _bounded_published_events,
    )

    boundary = datetime(2026, 11, 17, 5, 10, tzinfo=UTC)
    events = tuple(
        _event(boundary - timedelta(seconds=2) + timedelta(microseconds=i), i + 1)
        for i in range(_MAX_BATCH + 1)
    )
    selected, truncated = _bounded_published_events(
        events, causal_boundary=boundary,
    )
    assert len(selected) == _MAX_BATCH
    assert truncated is True


def test_provider_event_time_regression_blocks_boundary_split() -> None:
    from operations.connectivity.p90_r06_exit_owner import (
        P90R06ExitBlocked,
        _bounded_published_events,
    )

    boundary = datetime(2026, 11, 17, 5, 10, tzinfo=UTC)
    events = (
        _event(boundary - timedelta(seconds=2), 1),
        _event(boundary - timedelta(seconds=3), 2),
    )
    with pytest.raises(P90R06ExitBlocked, match="EVENT_TIME_REGRESSION"):
        _bounded_published_events(events, causal_boundary=boundary)


def test_source_lock_uses_r07_transition_guard_not_hot_session_row_lock() -> None:
    import inspect

    from operations.connectivity import p90_r06_exit_owner as owner

    body = inspect.getsource(owner._lock_source)
    assert "require_r07_dispatch_epoch_guard(" in body
    assert body.index("require_r07_dispatch_epoch_guard(") < body.index(
        "SELECT producer_release_commit,state,ended_at"
    )
    session_query = body.split(
        "SELECT producer_release_commit,state,ended_at", 1
    )[1].split('"""', 1)[0]
    assert "FOR SHARE" not in session_query


def test_prearm_result_surfaces_exact_exit_owner_evidence() -> None:
    source = (
        ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py"
    ).read_text(encoding="utf-8")
    assert '"p90_r06_exit_owner": exit_owner' in source


def test_cycle_holds_boundary_trade_until_closed_bar_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    fill = datetime(2026, 11, 17, 5, 1, tzinfo=UTC)
    boundary = datetime(2026, 11, 17, 5, 5, tzinfo=UTC)
    position = SimpleNamespace(
        fill_at=fill,
        symbol="LTCUSDT",
        strategy_position_id="p",
        strategy_id="p90l90_legacy_main_ltc_inj",
        trading_account_id="ta-" + "1" * 32,
    )
    scope = SimpleNamespace(
        identity=SimpleNamespace(origin_commit="a" * 40),
        routes=SimpleNamespace(account_by_strategy={
            "p90l90_legacy_main_ltc_inj": "ta-" + "1" * 32,
        }),
    )
    pre = _event(boundary - timedelta(microseconds=1), 1)
    at_boundary = _event(boundary, 2)
    snapshot = SimpleNamespace(
        closed_at=boundary - timedelta(minutes=5),
        events=(pre, at_boundary),
        truncated=False,
    )
    appended: list[tuple[object, ...]] = []

    monkeypatch.setattr(owner, "_runtime_scope", lambda *_a, **_k: scope)
    monkeypatch.setattr(owner, "_open_positions", lambda *_a, **_k: (position,))
    monkeypatch.setattr(owner, "lock_p90_exit_account", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_refresh_exit_claim", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_latest_mfe", lambda *_a, **_k: object())
    monkeypatch.setattr(owner, "_last_closed_boundary", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_source_snapshot", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(
        owner,
        "_append_mfe",
        lambda *_a, events, **_k: appended.append(events) or len(events),
    )
    monkeypatch.setattr(
        owner,
        "_process_closed",
        lambda *_a, **_k: pytest.fail("closed bar is not available yet"),
    )

    result = owner.run_p90_r06_exit_once(
        object(), object(), loaded_commit="b" * 40, now=boundary,
    )
    assert appended == [(pre,)]
    assert result == "P90_R06_EXIT_OWNER_RUNNING|P90_R06_EXIT_MFE_AWAITING_CLOSED:LTCUSDT:1"


def test_pending_closed_mutation_advances_only_processed_boundary_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    fill = datetime(2026, 11, 17, 5, 1, tzinfo=UTC)
    boundary = datetime(2026, 11, 17, 5, 5, tzinfo=UTC)
    position = SimpleNamespace(
        fill_at=fill,
        symbol="LTCUSDT",
        strategy_position_id="p",
        strategy_id="p90l90_legacy_main_ltc_inj",
        trading_account_id="ta-" + "1" * 32,
    )
    scope = SimpleNamespace(
        identity=SimpleNamespace(origin_commit="a" * 40),
        routes=SimpleNamespace(account_by_strategy={
            "p90l90_legacy_main_ltc_inj": "ta-" + "1" * 32,
        }),
    )
    snapshot = SimpleNamespace(
        closed_at=boundary,
        events=(),
        truncated=False,
    )
    marker: list[datetime] = []

    monkeypatch.setattr(owner, "_runtime_scope", lambda *_a, **_k: scope)
    monkeypatch.setattr(owner, "_open_positions", lambda *_a, **_k: (position,))
    monkeypatch.setattr(owner, "lock_p90_exit_account", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_refresh_exit_claim", lambda *_a, **_k: None)
    # First lookup says MFE exists; second lookup before closed processing too.
    monkeypatch.setattr(owner, "_latest_mfe", lambda *_a, **_k: object())
    monkeypatch.setattr(owner, "_last_closed_boundary", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_source_snapshot", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(owner, "_append_mfe", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        owner,
        "_process_closed",
        lambda *_a, **_k: "P90_R06_EXIT_CLOSED_BLOCKED",
    )
    monkeypatch.setattr(
        owner,
        "_record_pending_closed_boundary",
        lambda *_a, boundary, **_k: marker.append(boundary),
    )

    result = owner.run_p90_r06_exit_once(
        object(), object(), loaded_commit="b" * 40, now=boundary,
    )
    assert marker == [boundary]
    assert result.endswith("LTCUSDT:P90_R06_EXIT_CLOSED_BLOCKED")


def test_continuous_owner_preserves_firstfill_block_but_keeps_exit_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.connectivity import p90_r06_exit_owner as continuous
    from operations.connectivity import p90_real_independent_exit as firstfill
    from operations.connectivity import universal_exit_consumer as consumer

    calls: list[str] = []
    connection = SimpleNamespace(commit=lambda: calls.append("commit"))
    source = object()
    monkeypatch.setattr(consumer, "P90_R06_OWNER", "ENABLED")
    monkeypatch.setattr(consumer, "LOADED_COMMIT", "b" * 40)
    monkeypatch.setattr(
        firstfill,
        "run_p90_exit_once",
        lambda *_a, **_k: "P90_REAL_EXIT_FIRST_FILL_ORIGINAL_SOURCE_WINDOW_EXPIRED",
    )
    monkeypatch.setattr(
        firstfill,
        "audit_firstfill_exit_result",
        lambda *_a, **_k: calls.append("audit"),
    )
    monkeypatch.setattr(
        continuous,
        "run_p90_r06_exit_once",
        lambda *_a, **_k: calls.append("continuous")
        or "P90_R06_EXIT_OWNER_RUNNING|ARBUSDT:P90_R06_EXIT_MFE_AWAITING_CLOSED:0",
    )

    result = consumer.run_once(
        connection,
        source_connection=source,
        run_generic=False,
        now=datetime(2026, 11, 17, 5, 0, tzinfo=UTC),
    )
    assert result.startswith("P90_R06_FIRSTFILL_OWNER_BLOCKED|")
    assert "CONTINUOUS=P90_R06_EXIT_OWNER_RUNNING|" in result
    assert calls == ["audit", "commit", "continuous"]


def test_first_mfe_source_snapshot_rejects_prefill_event_after_anchor() -> None:
    source = (
        ROOT / "operations/connectivity/p90_r06_exit_owner.py"
    ).read_text(encoding="utf-8")
    assert "P90_R06_EXIT_PREFILL_EVENT_AFTER_ANCHOR" in source
    assert ">= position.fill_at.astimezone(UTC)" in source
