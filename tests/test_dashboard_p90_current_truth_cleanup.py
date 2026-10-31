from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")


def test_right_closed_trade_rail_is_removed_from_active_workspace():
    assert 'id="closedWorkspaceRail"' in INDEX
    assert 'id="closedWorkspaceRail" class="workspace-rail"' in INDEX
    assert 'aria-label="Завершённые сделки и результат" hidden' in INDEX
    assert "#closedWorkspaceRail{display:none!important}" in INDEX
    assert "grid-template-columns:minmax(0,18fr) minmax(0,82fr)" in INDEX
    assert "closedRail.hidden||railRefreshBusy" in INDEX


def test_legacy_generic_gate_controls_are_not_visible():
    hidden_css = (
        "#tradeGateButton,#tradeOperationStatus,#tradeGateNotice,"
        "#tradeGateReasons,#gatewayReleaseStatus{display:none!important}"
    )
    assert hidden_css in INDEX
    assert 'id="tradeGateButton"' in INDEX
    assert "P90 REAL v3 OWNER ARM" in INDEX
    assert "P90 PREARM cohort найден" not in INDEX


def test_p90_owner_arm_status_uses_current_runtime_truth():
    assert '"p90_arm_status": p90_arm_status' in APP
    assert '"REMOTE_SOURCE_INSTALLED_IDENTITY_DRIFT"' in APP
    assert '"P90_REAL_OBSERVER_NOT_READY"' in APP
    assert '"loaded_commit": LOADED_RELEASE_COMMIT' in APP
    assert '"source_head": source_head' in APP
    assert 'window.p90ArmStatus=lt.p90_arm_status||null' in INDEX
    assert "source/runtime release identity не совпадает" in INDEX
    assert "Universal Entry observer сейчас не готов" in INDEX


def test_superseded_observer_faults_stay_durable_but_leave_current_console():
    assert "historical_open_count = len(items)" in APP
    assert "superseded_count += 1" in APP
    assert 'str(observer.get("state") or "") == "RUNNING"' in APP
    assert 'bool(observer.get("observer_ready"))' in APP
    assert '"historical_open_count": historical_open_count' in APP
    assert '"superseded_count": superseded_count' in APP
    assert "DELETE FROM runtime.lifecycle_faults" not in APP


def test_current_console_keeps_exact_p90_scope_visible():
    assert "Scope: MAIN LTC/INJ · SUB DOT/ARB/APT" in INDEX
    assert "10 USDT на Entry · isolated 1x · caps 20/30 USDT" in INDEX
    assert "OWNER ARM всегда повторно проверяет full server evidence" in INDEX
    assert "Mainnet gate CLOSED · реальные Entry сейчас запрещены" in INDEX
