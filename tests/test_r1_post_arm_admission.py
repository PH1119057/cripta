from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from operations.connectivity import universal_entry_consumer as entry_consumer
from operations.monitoring import universal_entry_shadow as observer

ROOT = Path(__file__).resolve().parents[1]


def test_real_execution_activation_does_not_reuse_expiring_prearm_evidence() -> None:
    source = (
        ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _real_execution_activation_ids(")
    end = source.index("\ndef _real_entry_account_state(", start)
    body = source[start:end]
    assert "evaluate_live_arm(" not in body
    assert "active_live_arm_session(connection, context=context)" in body
    assert "execution_permissions" in body
    assert "control.execution_gates" in body


def test_per_entry_safety_uses_complete_generation_and_remains_fail_closed() -> None:
    source = (
        ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _real_entry_account_state(")
    end = source.index("\ndef _observer_objective_inputs(", start)
    body = source[start:end]
    assert "current_complete_account_state_generation" in body
    assert "generation.position_mode_refs.get(symbol)" in body
    assert "runtime.lifecycle_faults" in body
    assert "runtime.reconciliation_runs" not in body
    assert "runtime.wallet_latest" not in body
    assert "max age" not in body

    admission = (
        ROOT / "src/bybit_workbench/entry_admission.py"
    ).read_text(encoding="utf-8")
    assert "current_complete_account_state_generation" in admission
    assert "request.account_state_generation_id" in admission
    assert "generation.position_mode_refs.get(request.symbol" in admission
    assert 'position_mode != "ONE_WAY"' in admission

def test_real_entry_readiness_uses_admission_time_not_market_fact_time() -> None:
    observer = (
        ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    start = observer.index("def _run_observer_epoch(")
    body = observer[start:]
    assert "datetime.now(UTC) if real_execution_selected_for else fact.observed_at" in body
    assert "observed_at=admission_time" in body
    assert "admission_time=admission_time" in body

    engine = (
        ROOT / "src/bybit_workbench/universal_entry/engine.py"
    ).read_text(encoding="utf-8")
    assert "admission_time: datetime | None = None" in engine
    assert "admission_time or fact_for_predicate.observed_at" in engine


def test_real_entry_consumer_uses_active_session_not_expiring_prearm_evidence() -> None:
    source = (
        ROOT / "operations/connectivity/universal_entry_consumer.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _live_arm_ready(")
    end = source.index("\ndef run_once(", start)
    body = source[start:end]
    assert "evaluate_live_arm(" not in body
    assert "active_live_arm_session(connection, context=context)" in body

    run_start = source.index("def run_once(")
    run_body = source[run_start:]
    assert run_body.index("_admission_pre_dispatch_status(") < run_body.index(
        "_live_arm_ready("
    )
    assert "ADMISSION_ACCOUNT_GENERATION_CHANGED" in source
    assert "POSITION_MODE_STATE_STALE" not in source
    assert "EXCHANGE_POSITION_MODE_MISMATCH" in source


class _SessionCursor:
    def __init__(
        self, row: tuple[str] | None,
        rows: tuple[dict[str, str], ...] = (),
    ) -> None:
        self._row = row
        self._rows = rows

    def fetchone(self) -> tuple[str] | None:
        return self._row

    def fetchall(self) -> tuple[dict[str, str], ...]:
        return self._rows


class _ActiveSessionOnlyConnection:
    def execute(self, statement: str, parameters: object = ()) -> _SessionCursor:
        assert "live_arm_evidence" not in statement
        if "FROM strategy_entry.strategy_execution_bindings" in statement:
            return _SessionCursor(
                None, ({"trading_account_id": "test-account", "account_kind": "MAIN"},)
            )
        if "FROM control.live_arm_sessions" in statement:
            return _SessionCursor(("arm-session",))
        raise AssertionError(f"unexpected query: {statement}")


def test_real_entry_consumer_accepts_exact_active_session_without_prearm_recheck(
    monkeypatch,
) -> None:
    monkeypatch.setattr(entry_consumer, "LOADED_RELEASE_COMMIT", "a" * 40)
    ready, failed = entry_consumer._live_arm_ready(
        _ActiveSessionOnlyConnection(),
        {
            "strategy_id": "r1_aptusdt",
            "strategy_version": "1.1-micro-live",
            "strategy_config_fingerprint": "cfg",
            "activation_id": "activation",
            "symbol": "APTUSDT",
            "trading_account_id": "test-account",
        },
        now=datetime(2026, 10, 7, 7, 30, tzinfo=UTC),
    )
    assert ready is True
    assert failed == ()


def test_pre_dispatch_reproves_exact_latest_account_generation() -> None:
    request = SimpleNamespace(
        trading_account_id="account-main",
        symbol="BTCUSDT",
        position_mode_state_ref="mode-btc",
    )
    current = SimpleNamespace(
        generation_id="generation-main",
        position_mode_refs={"BTCUSDT": "mode-btc"},
    )
    row = {"capital_capacity_snapshot_id": "generation-main"}
    with patch.object(
        entry_consumer, "current_complete_account_state_generation",
        return_value=current,
    ) as load:
        result = entry_consumer._generation_pre_dispatch_status(
            object(), row, request,
        )
    assert result == ("OK", "")
    assert load.call_args.kwargs == {
        "account_ref": "BYBIT:UNIFIED",
        "trading_account_id": "account-main",
        "acquire_account_lock": True,
    }


def test_pre_dispatch_blocks_obsolete_or_cross_account_generation() -> None:
    request = SimpleNamespace(
        trading_account_id="account-sub",
        symbol="BTCUSDT",
        position_mode_state_ref="mode-sub",
    )
    current = SimpleNamespace(
        generation_id="generation-sub",
        position_mode_refs={"BTCUSDT": "mode-sub"},
    )
    with patch.object(
        entry_consumer, "current_complete_account_state_generation",
        return_value=current,
    ):
        assert entry_consumer._generation_pre_dispatch_status(
            object(), {"capital_capacity_snapshot_id": "generation-main"},
            request,
        ) == ("STALE_MODE", "ADMISSION_ACCOUNT_GENERATION_CHANGED")
        assert entry_consumer._generation_pre_dispatch_status(
            object(), {"capital_capacity_snapshot_id": "generation-sub"},
            SimpleNamespace(
                trading_account_id="account-sub",
                symbol="BTCUSDT",
                position_mode_state_ref="mode-main",
            ),
        ) == ("STALE_MODE", "ADMISSION_ACCOUNT_GENERATION_CHANGED")


def test_pre_dispatch_blocks_failed_terminal_generation() -> None:
    from bybit_workbench.account_state_generation import (
        AccountStateGenerationUnavailable,
    )

    request = SimpleNamespace(
        trading_account_id="account-sub",
        symbol="BTCUSDT",
        position_mode_state_ref="mode-sub",
    )
    with patch.object(
        entry_consumer, "current_complete_account_state_generation",
        side_effect=AccountStateGenerationUnavailable("FAILED"),
    ):
        assert entry_consumer._generation_pre_dispatch_status(
            object(), {"capital_capacity_snapshot_id": "generation-sub"},
            request,
        ) == ("STALE_MODE", "CURRENT_ACCOUNT_GENERATION_UNAVAILABLE")


def test_real_consumer_commits_every_entry_iteration(monkeypatch) -> None:
    # An unbroken stream of DISPATCHED/BLOCKED results must not retain
    # uncommitted queued commands, capital locks, or request state.
    events: list[str] = []

    class OfflineConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def commit(self):
            events.append("COMMIT")

    fake_connection = OfflineConnection()
    outcomes = iter(("DISPATCHED:entry-a", "BLOCKED:MODE", "NO_REQUEST"))

    def fake_run_once(_connection, *, now=None):
        del now
        result = next(outcomes)
        events.append(result)
        if result == "NO_REQUEST":
            monkeypatch.setattr(entry_consumer, "running", False)
        return result

    with (
        patch.object(entry_consumer, "ENTRY_COMMAND_SOURCE", "UNIVERSAL_ENTRY"),
        patch.object(entry_consumer, "CONSUMER_ARM", "ENABLED"),
        patch.object(entry_consumer, "TRADING_ACCOUNT_ID", "test-main"),
        patch.object(entry_consumer, "ACCOUNT_CONNECTOR_ID", "test-connector"),
        patch.object(entry_consumer, "POLL_SECONDS", 0.01),
        patch.object(entry_consumer, "HEALTH_SECONDS", 2.0),
        patch.object(entry_consumer, "running", True),
        patch.object(entry_consumer, "require_exact_sub_instance"),
        patch.object(
            entry_consumer,
            "_consumer_runtime_readiness",
            return_value=(True, ()),
        ),
        patch.object(entry_consumer, "_write_consumer_health"),
        patch.object(
            entry_consumer,
            "_acknowledge_owner_diagnostic_entry_plans",
            return_value=0,
        ),
        patch.object(entry_consumer.signal, "signal"),
        patch.object(
            entry_consumer.psycopg, "connect", return_value=fake_connection
        ),
        patch.object(entry_consumer, "check_release_arm_invariant"),
        patch.object(entry_consumer, "run_once", side_effect=fake_run_once),
        patch.object(entry_consumer.time, "sleep"),
        patch.object(entry_consumer.time, "monotonic", return_value=100.0),
    ):
        assert entry_consumer.main() == 0

    assert events == [
        "COMMIT", "DISPATCHED:entry-a", "COMMIT",
        "BLOCKED:MODE", "COMMIT",
        "NO_REQUEST", "COMMIT",
    ]


def test_consumer_readiness_is_bounded_to_health_cadence(monkeypatch) -> None:
    events: list[str] = []

    class OfflineConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def commit(self):
            events.append("COMMIT")

    outcomes = iter(("NO_REQUEST", "NO_REQUEST", "NO_REQUEST"))

    def fake_run_once(_connection, *, now=None):
        del now
        result = next(outcomes)
        events.append(result)
        if events.count("NO_REQUEST") == 3:
            monkeypatch.setattr(entry_consumer, "running", False)
        return result

    with (
        patch.object(entry_consumer, "ENTRY_COMMAND_SOURCE", "UNIVERSAL_ENTRY"),
        patch.object(entry_consumer, "CONSUMER_ARM", "ENABLED"),
        patch.object(entry_consumer, "TRADING_ACCOUNT_ID", "test-main"),
        patch.object(entry_consumer, "ACCOUNT_CONNECTOR_ID", "test-connector"),
        patch.object(entry_consumer, "POLL_SECONDS", 0.01),
        patch.object(entry_consumer, "HEALTH_SECONDS", 2.0),
        patch.object(entry_consumer, "running", True),
        patch.object(entry_consumer, "require_exact_sub_instance"),
        patch.object(
            entry_consumer,
            "_consumer_runtime_readiness",
            return_value=(True, ()),
        ) as readiness,
        patch.object(entry_consumer, "_write_consumer_health") as health,
        patch.object(
            entry_consumer,
            "_acknowledge_owner_diagnostic_entry_plans",
            return_value=0,
        ),
        patch.object(entry_consumer.signal, "signal"),
        patch.object(
            entry_consumer.psycopg,
            "connect",
            return_value=OfflineConnection(),
        ),
        patch.object(entry_consumer, "check_release_arm_invariant") as guard,
        patch.object(entry_consumer, "run_once", side_effect=fake_run_once),
        patch.object(entry_consumer.time, "sleep"),
        patch.object(
            entry_consumer.time,
            "monotonic",
            side_effect=(100.0, 100.1, 102.1),
        ),
    ):
        assert entry_consumer.main() == 0

    assert readiness.call_count == 2
    assert health.call_count == 2
    assert guard.call_count == 2


def test_observer_real_entry_faults_are_scoped_per_account() -> None:
    now = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    generation = SimpleNamespace(
        generation_id="gen-sub",
        position_mode_refs={"BTCUSDT": "mode-sub"},
        completed_at=now,
        available_balance=Decimal("10"),
    )

    class FaultDB:
        def __init__(self, account: str | None) -> None:
            self.account = account

        def execute(self, sql: str, params: tuple[str, ...]):
            assert "runtime.lifecycle_faults" in sql
            assert "NULLIF(exact_ids->>'trading_account_id','')" in sql
            assert params == ("ta-sub",)
            row = (
                ("EXACT_CRITICAL",)
                if self.account is None or self.account == "ta-sub"
                else None
            )
            return SimpleNamespace(fetchone=lambda: row)

    with patch.object(
        observer, "current_complete_account_state_generation",
        return_value=generation,
    ) as fetch:
        _ready, allowed = observer._real_entry_account_state(
            FaultDB("ta-main"),
            symbol="BTCUSDT", observed_at=now, trading_account_id="ta-sub",
        )
        assert allowed is not None
        assert allowed.capacity_snapshot_id == "gen-sub"
        for scope in ("ta-sub", None):
            _blocked, capacity = observer._real_entry_account_state(
                FaultDB(scope),
                symbol="BTCUSDT", observed_at=now, trading_account_id="ta-sub",
            )
            assert capacity is None
    assert fetch.call_args.kwargs["trading_account_id"] == "ta-sub"


def test_observer_real_entry_rejects_missing_account_without_generation() -> None:
    with (
        patch.object(observer, "TRADING_ACCOUNT_ID", ""),
        patch.object(observer, "current_complete_account_state_generation") as fetch,
    ):
        _readiness, capacity = observer._real_entry_account_state(
            object(),
            symbol="BTCUSDT",
            observed_at=datetime(2026, 10, 25, tzinfo=UTC),
        )
    assert capacity is None
    fetch.assert_not_called()
