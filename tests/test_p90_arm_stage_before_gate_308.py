from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = (
    ROOT / "src/bybit_workbench/p90l90_real_staged_arm.py"
).read_text(encoding="utf-8")
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
UI = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
OBSERVER = (
    ROOT / "operations/monitoring/universal_entry_shadow.py"
).read_text(encoding="utf-8")


def test_stage_never_opens_mainnet_gate() -> None:
    stage = STAGED[
        STAGED.index("def stage_p90l90_real_v3("):
        STAGED.index("\ndef finalize_p90l90_real_v3(")
    ]
    assert "SET enabled=0,reason=%s" in stage
    assert "OWNER_ARM_STAGE_GATE_REASON" in stage
    assert "SET enabled=1" not in stage
    assert '"gate_enabled": False' in stage


def test_finalize_is_the_only_staged_path_that_opens_gate() -> None:
    finalize = STAGED[STAGED.index("def finalize_p90l90_real_v3("):]
    assert "observer_stage_ready" in finalize
    assert "P90_REAL_OPEN_RUNTIME_INCIDENT" in finalize
    assert "P90_REAL_ENTRY_CONSUMER_NOT_READY" in finalize
    assert "OWNER_ARM_STAGE_GATE_REASON" in finalize
    assert "SET enabled=1,reason=%s" in finalize
    assert "OWNER_ARM_OPEN_GATE_REASON" in finalize


def test_dashboard_waits_for_exact_stage_and_disarms_on_failure() -> None:
    handler = APP[
        APP.index("def _u6_p90_real_v3_arm("):
        APP.index("\ndef _u6_financial_account_catalog")
    ]
    assert "stage_p90l90_real_v3(" in handler
    assert "_u6_wait_p90_real_v3_stage_ready(" in handler
    assert "timeout_seconds=120.0" in handler
    assert "final_stage_ready" in handler
    assert "finalize_p90l90_real_v3(" in handler
    assert "if staged:" in handler
    assert "disarm_p90l90_real_v3(" in handler
    assert handler.index("stage_p90l90_real_v3(") < handler.index(
        "_u6_wait_p90_real_v3_stage_ready("
    ) < handler.index("finalize_p90l90_real_v3(")


def test_stage_readiness_requires_running_observer_consumers_and_no_incident() -> None:
    start = APP.index("def _u6_p90_real_v3_stage_ready(")
    end = APP.index("\ndef _u6_wait_p90_real_v3_stage_ready(", start)
    body = APP[start:end]
    assert 'payload.get("state") == "RUNNING"' in body
    assert 'payload.get("observer_ready") is True' in body
    assert 'payload.get("p90_owner_arm_stage") == "READY"' in body
    assert "P90_OWNER_ARM_STAGE_GATE_REASON" in body
    assert "runtime.entry_consumer_health" in body
    assert "release_arm_incidents" in body
    assert "state='OPEN'" in body


def test_observer_stage_builds_full_runtime_but_exposes_no_admission() -> None:
    assert "p90_real_staged = True" in OBSERVER
    assert '"p90_real_staged_gate_closed"' in OBSERVER
    assert "staged_gate_closed" in OBSERVER
    assert "return (), False" in OBSERVER
    assert "OWNER_ARM_OPEN_GATE_REASON" in OBSERVER


def test_ui_explains_gate_closed_stage_and_allows_bounded_wait() -> None:
    assert "проверяется полный runtime · mainnet gate пока CLOSED" in UI
    assert "180000" in UI
    assert "Если проверка не пройдёт — gate не откроется." in UI
