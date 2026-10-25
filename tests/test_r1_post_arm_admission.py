from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
