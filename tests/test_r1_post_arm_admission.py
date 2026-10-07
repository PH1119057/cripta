from datetime import UTC, datetime
from pathlib import Path

from operations.connectivity import universal_entry_consumer as entry_consumer

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
    assert "POSITION_MODE_STATE_STALE" in source
    assert "EXCHANGE_POSITION_MODE_MISMATCH" in source


class _SessionCursor:
    def __init__(self, row: tuple[str] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[str] | None:
        return self._row


class _ActiveSessionOnlyConnection:
    def execute(self, statement: str, parameters: object = ()) -> _SessionCursor:
        assert "live_arm_evidence" not in statement
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
            "strategy_version": "1.0-micro-live",
            "strategy_config_fingerprint": "cfg",
            "activation_id": "activation",
            "symbol": "APTUSDT",
        },
        now=datetime(2026, 10, 7, 7, 30, tzinfo=UTC),
    )
    assert ready is True
    assert failed == ()
