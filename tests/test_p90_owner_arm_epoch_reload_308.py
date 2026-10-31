from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBSERVER = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(
    encoding="utf-8"
)
LIFECYCLE = (
    ROOT / "operations/monitoring/lifecycle_supervisor_runtime.py"
).read_text(encoding="utf-8")


def test_exact_owner_arm_transition_is_clean_epoch_reload_not_runtime_fault() -> None:
    assert "class ObserverEpochReload(Exception):" in OBSERVER
    start = OBSERVER.index("def _require_p90_source_only_epoch_current(")
    end = OBSERVER.index(
        "\ndef _unarmed_multi_account_candidate_bindings(", start
    )
    body = OBSERVER[start:end]
    assert "selected = _real_execution_selected_activation_ids" in body
    assert "gate_enabled = bool(gate and gate[0])" in body
    assert "if not gate_enabled and not selected:" in body
    assert "if allow_live_transition and gate_enabled and selected == expected:" in body
    assert 'raise ObserverEpochReload("P90_REAL_OWNER_ARM_TO_LIVE_RELOAD")' in body
    assert 'raise RuntimeError("P90_REAL_SOURCE_ONLY_EXECUTION_GATE_DRIFT")' in body


def test_forced_source_only_does_not_gain_live_transition_exception() -> None:
    start = OBSERVER.index("def _run_observer_epoch(")
    end = OBSERVER.index("\ndef _run_multi_strategy_observer()", start)
    body = OBSERVER[start:end]
    assert "allow_live_transition=not p90_real_force_source_only" in body
    assert "except ObserverEpochReload as reload_exc:" in body
    assert 'state="RELOADING"' in body
    assert 'reason=str(reload_exc)' in body
    assert 'return "RELOAD"' in body


def test_lifecycle_accepts_only_bounded_exact_owner_arm_reload() -> None:
    start = LIFECYCLE.index("def _entry_source_health(")
    end = LIFECYCLE.index("\ndef _owner_alert_sender(", start)
    body = LIFECYCLE[start:end]
    assert 'state == "RELOADING"' in body
    assert 'reason == "P90_REAL_OWNER_ARM_TO_LIVE_RELOAD"' in body
    assert '(state == "RUNNING" or expected_arm_reload)' in body
    assert "max_age_seconds = 60 if expected_arm_reload else 15" in body
    assert "0 <= age <= max_age_seconds" in body
    assert '"WARMUP"' not in body


def test_generic_topology_reload_remains_distinct_from_owner_arm_reload() -> None:
    assert 'reload_reason = "StrategyActivation set changed"' in OBSERVER
    assert "except ObserverEpochReload as reload_exc:" in OBSERVER
    assert "reload_reason = str(reload_exc)" in OBSERVER
