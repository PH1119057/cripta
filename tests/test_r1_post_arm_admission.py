from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_real_execution_activation_does_not_reuse_expiring_prearm_evidence() -> None:
    source = (
        ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _real_execution_activation_ids(")
    end = source.index("\ndef _real_entry_technical_readiness(", start)
    body = source[start:end]
    assert "evaluate_live_arm(" not in body
    assert "active_live_arm_session(connection, context=context)" in body
    assert "execution_permissions" in body
    assert "control.execution_gates" in body


def test_per_entry_safety_remains_fresh_and_fail_closed() -> None:
    source = (
        ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _real_entry_technical_readiness(")
    end = source.index("\ndef _observer_objective_inputs(", start)
    body = source[start:end]
    assert "runtime.reconciliation_runs" in body
    assert "runtime.wallet_latest" in body
    assert "runtime.lifecycle_faults" in body
    assert "real Entry account-state max age" in body

    admission = (
        ROOT / "src/bybit_workbench/entry_admission.py"
    ).read_text(encoding="utf-8")
    assert "runtime.position_mode_states" in admission
    assert 'position_mode != "ONE_WAY"' in admission
    assert "fresh_until.astimezone(UTC) < now" in admission
