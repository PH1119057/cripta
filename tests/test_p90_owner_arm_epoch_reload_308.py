import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBSERVER = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(
    encoding="utf-8"
)
LIFECYCLE = (
    ROOT / "operations/monitoring/lifecycle_supervisor_runtime.py"
).read_text(encoding="utf-8")


def _source_only_gate_compatible():
    tree = ast.parse(OBSERVER)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_p90_source_only_gate_compatible"
    )
    namespace = {
        "OWNER_FULL_CONTOUR_ENTRY_WINDOW_GATE_REASON":
            "owner full-contour diagnostic Entry window",
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), "observer", "exec"),
        namespace,
    )
    return namespace["_p90_source_only_gate_compatible"]


def _diagnostic_signature_transition_compatible():
    tree = ast.parse(OBSERVER)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_owner_full_contour_signature_transition_compatible"
    )
    namespace = {
        "OWNER_FULL_CONTOUR_ENTRY_WINDOW_GATE_REASON":
            "owner full-contour diagnostic Entry window",
        "OWNER_FULL_CONTOUR_GATE_CLOSE_PREFIX":
            "owner full-contour diagnostic Entry window closed:",
        "OWNER_FULL_CONTOUR_SIGNATURE_STRATEGY_IDS": frozenset(
            {"owner_full_contour_diag_main", "owner_full_contour_diag_sub"}
        ),
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), "observer", "exec"),
        namespace,
    )
    return namespace["_owner_full_contour_signature_transition_compatible"]


def _signature(
    *,
    gate: bool,
    diagnostic_permission: bool,
    diagnostic_permission_updated: str,
    p90_permission: bool = False,
    p90_permission_updated: str = "p90-perm-1",
    diagnostic_activation_updated: str = "diag-act-1",
    binding_state: str = "ACTIVE",
) -> tuple[tuple[str, ...], ...]:
    return (
        ("MAINNET_GATE", str(gate)),
        (
            "activation-owner-full-contour-sub-v2-20261101",
            "owner_full_contour_diag_sub",
            "2.0-owner-full-contour-e2e-20261101",
            "diag-fp",
            diagnostic_activation_updated,
            "diag-entry",
            "diag-exit",
            str(diagnostic_permission),
            diagnostic_permission_updated,
        ),
        (
            "activation-p90l90-real-sub-20261029-v3",
            "p90l90_legacy_sub_dot_arb_apt",
            "2.0-p90l90-real-main-sub-20261028",
            "p90-fp",
            "p90-act-1",
            "p90-entry",
            "p90-exit",
            str(p90_permission),
            p90_permission_updated,
        ),
        (
            "BINDING",
            "binding-p90l90-real-sub-20261029-v3",
            "activation-p90l90-real-sub-20261029-v3",
            "PRIMARY",
            "PRIMARY",
            "ta-sub",
            "DEDICATED_STRATEGY",
            binding_state,
            "effective-from",
            "",
            "binding-updated",
        ),
    )


def test_exact_diagnostic_open_and_close_do_not_reload_observer_epoch() -> None:
    compatible = _diagnostic_signature_transition_compatible()
    before = _signature(
        gate=False,
        diagnostic_permission=False,
        diagnostic_permission_updated="diag-perm-1",
    )
    opened = _signature(
        gate=True,
        diagnostic_permission=True,
        diagnostic_permission_updated="diag-perm-2",
    )
    closed = _signature(
        gate=False,
        diagnostic_permission=False,
        diagnostic_permission_updated="diag-perm-3",
    )
    assert compatible(
        before,
        opened,
        gate_reason="owner full-contour diagnostic Entry window",
    )
    assert compatible(
        opened,
        closed,
        gate_reason=(
            "owner full-contour diagnostic Entry window closed: "
            "diagnostic Entry dispatched"
        ),
    )


def test_diagnostic_signature_exception_rejects_real_topology_or_parent_changes() -> None:
    compatible = _diagnostic_signature_transition_compatible()
    before = _signature(
        gate=False,
        diagnostic_permission=False,
        diagnostic_permission_updated="diag-perm-1",
    )
    opened = _signature(
        gate=True,
        diagnostic_permission=True,
        diagnostic_permission_updated="diag-perm-2",
    )
    assert not compatible(
        before,
        _signature(
            gate=True,
            diagnostic_permission=True,
            diagnostic_permission_updated="diag-perm-2",
            p90_permission=True,
            p90_permission_updated="p90-perm-2",
        ),
        gate_reason="owner full-contour diagnostic Entry window",
    )
    assert not compatible(
        before,
        _signature(
            gate=True,
            diagnostic_permission=True,
            diagnostic_permission_updated="diag-perm-2",
            binding_state="SUSPENDED",
        ),
        gate_reason="owner full-contour diagnostic Entry window",
    )
    assert not compatible(
        before,
        _signature(
            gate=True,
            diagnostic_permission=True,
            diagnostic_permission_updated="diag-perm-2",
            diagnostic_activation_updated="diag-act-2",
        ),
        gate_reason="owner full-contour diagnostic Entry window",
    )
    assert not compatible(
        before,
        opened,
        gate_reason="unrelated gate reason",
    )


def test_exact_diagnostic_signature_transition_bypasses_reload_block() -> None:
    start = OBSERVER.index("if latest_signature != current_signature:")
    end = OBSERVER.index('reload_reason = "StrategyActivation set changed"', start)
    body = OBSERVER[start:end]
    assert "_owner_full_contour_signature_transition_compatible(" in body
    assert "current_signature = latest_signature" in body
    assert "next_signature = now_mono + 1.0" in body
    assert "continue" in body


def test_owner_diagnostic_gate_keeps_parent_p90_source_only_epoch_stable() -> None:
    compatible = _source_only_gate_compatible()
    assert compatible(gate_enabled=False, gate_reason="", selected=frozenset())
    assert compatible(
        gate_enabled=True,
        gate_reason="owner full-contour diagnostic Entry window",
        selected=frozenset(),
    )
    assert not compatible(
        gate_enabled=True,
        gate_reason="owner full-contour diagnostic Entry window",
        selected=frozenset({"activation-p90"}),
    )
    assert not compatible(
        gate_enabled=True,
        gate_reason="unrelated gate reason",
        selected=frozenset(),
    )


def test_source_only_guards_use_same_diagnostic_compatibility_predicate() -> None:
    assert OBSERVER.count("_p90_source_only_gate_compatible(") >= 2
    assert "OWNER_FULL_CONTOUR_ENTRY_WINDOW_GATE_REASON" in OBSERVER
    assert "elif not gate_enabled and not selected:" in OBSERVER


def test_exact_owner_arm_stage_is_clean_epoch_reload_not_runtime_fault() -> None:
    assert "class ObserverEpochReload(Exception):" in OBSERVER
    start = OBSERVER.index("def _require_p90_source_only_epoch_current(")
    end = OBSERVER.index(
        "\ndef _unarmed_multi_account_candidate_bindings(", start
    )
    body = OBSERVER[start:end]
    assert "selected = _real_execution_selected_activation_ids" in body
    assert "gate_enabled = bool(gate and gate[0])" in body
    assert "gate_reason == OWNER_ARM_STAGE_GATE_REASON" in body
    assert "selected == expected" in body
    assert "raise ObserverEpochReload(OWNER_ARM_STAGE_RELOAD_REASON)" in body
    assert 'raise RuntimeError("P90_REAL_SOURCE_ONLY_EXECUTION_GATE_DRIFT")' in body


def test_forced_source_only_does_not_gain_staged_transition_exception() -> None:
    start = OBSERVER.index("def _run_observer_epoch(")
    end = OBSERVER.index("\ndef _run_multi_strategy_observer()", start)
    body = OBSERVER[start:end]
    assert "allow_live_transition=not p90_real_force_source_only" in body
    assert "except ObserverEpochReload as reload_exc:" in body
    assert 'state="RELOADING"' in body
    assert 'reason=str(reload_exc)' in body
    assert 'return "RELOAD"' in body


def test_lifecycle_accepts_only_bounded_exact_owner_arm_stage_reload() -> None:
    start = LIFECYCLE.index("def _entry_source_health(")
    end = LIFECYCLE.index("\ndef _owner_alert_sender(", start)
    body = LIFECYCLE[start:end]
    assert 'state == "RELOADING"' in body
    assert "reason == OWNER_ARM_STAGE_RELOAD_REASON" in body
    assert '(state == "RUNNING" or expected_arm_reload)' in body
    assert "max_age_seconds = 60 if expected_arm_reload else 15" in body
    assert "0 <= age <= max_age_seconds" in body
    assert '"WARMUP"' not in body


def test_stage_gate_open_is_consumed_without_second_epoch_gap() -> None:
    assert "p90_real_staged = True" in OBSERVER
    assert "staged_gate_closed" in OBSERVER
    assert "staged_gate_finalized" in OBSERVER
    assert "return (), False" in OBSERVER
    assert "latest_signature[1:] == current_signature[1:]" in OBSERVER
    assert 'reason="P90_REAL_OWNER_ARM_GATE_OPEN"' in OBSERVER
    assert "p90_real_staged = False" in OBSERVER


def test_generic_topology_reload_remains_distinct_from_owner_arm_stage() -> None:
    assert 'reload_reason = "StrategyActivation set changed"' in OBSERVER
    assert "except ObserverEpochReload as reload_exc:" in OBSERVER
    assert "reload_reason = str(reload_exc)" in OBSERVER
