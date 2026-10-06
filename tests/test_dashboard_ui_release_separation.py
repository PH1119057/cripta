from pathlib import Path


DEV = Path("docs/DEVELOPMENT_RELEASE_RULES_RU.md").read_text(encoding="utf-8")
ARCH = Path("docs/CRIPTA_ARCHITECTURE_RULES_RU_V1.md").read_text(encoding="utf-8")
OBS = Path("docs/OBSERVATION_ANALYTICS_RU.md").read_text(encoding="utf-8")
MAP = Path("docs/CURRENT_PROJECT_MAP_RU.md").read_text(encoding="utf-8")
INSTALLER = Path("operations/infrastructure/install_verified_release.sh").read_text(encoding="utf-8")
UI_DEPLOY = Path("operations/infrastructure/deploy_dashboard_ui.sh").read_text(encoding="utf-8")


def test_canon_separates_presentation_ui_from_trading_runtime() -> None:
    assert "Presentation/read-model-only изменение Dashboard" in DEV
    assert "не должно останавливать реальную торговлю" in DEV
    assert "DASHBOARD_UI_COMMIT" in DEV
    assert "Presentation UI boundary" in ARCH
    assert "presentation HTML/CSS и read-only Dashboard projections/aggregations" in OBS
    assert "Dashboard UI release separation" in MAP


def test_ui_deploy_does_not_restart_or_disarm_trading_services() -> None:
    assert "systemctl stop" not in UI_DEPLOY
    assert "systemctl restart cripta-dashboard.service" in UI_DEPLOY
    assert "UPDATE control.execution_gates" not in UI_DEPLOY
    assert "UPDATE strategy_entry.execution_permissions" not in UI_DEPLOY
    assert "GATE_UNCHANGED" in UI_DEPLOY
    assert "EXECUTION_PERMISSIONS_UNCHANGED" in UI_DEPLOY
    assert "DASHBOARD_PRESENTATION_READ_MODEL_SCOPE=PASS" in UI_DEPLOY
    assert "Dashboard deploy changed trading service PID" in UI_DEPLOY
    assert "Dashboard deploy restarted trading service" in UI_DEPLOY
    assert "Dashboard app must be a regular in-directory file" in UI_DEPLOY


def test_full_runtime_installer_preserves_independent_ui_identity() -> None:
    assert 'DASHBOARD_UI_ROOT="${CRIPTA_DASHBOARD_UI_ROOT:-/srv/cripta/dashboard-ui}"' in INSTALLER
    assert 'ln -s "$DASHBOARD_UI_ROOT/current/index.html"' in INSTALLER
    assert 'install -o cripta -g cripta -m 0644 "$DASHBOARD_UI_ROOT/current/app.py"' in INSTALLER
    assert "Dashboard app must remain a regular in-directory file" in INSTALLER
    assert "/usr/local/sbin/cripta-deploy-dashboard-ui" in INSTALLER
    assert "CRIPTA_DASHBOARD_UI_ROOT=$DASHBOARD_UI_ROOT" in INSTALLER



def test_ui_verifier_allows_only_stage6b_fault_resolution_extension() -> None:
    assert 'allowed_added_endpoints = {"/api/observer-faults/resolve"}' in UI_DEPLOY
    assert '"OBSERVER_RUNTIME_FAULT_CODE"' in UI_DEPLOY
    assert '"observer_runtime_fault_state"' in UI_DEPLOY
    assert '"merge_observer_fault_health"' in UI_DEPLOY
    assert '"resolve_observer_runtime_fault"' in UI_DEPLOY
    assert '"UPDATE runtime.lifecycle_faults" not in value' in UI_DEPLOY
    assert 'changed_methods != ["do_POST"]' in UI_DEPLOY
    assert '"/api/live/"' in UI_DEPLOY
    assert "trading control added to Handler.do_POST" in UI_DEPLOY


def test_ui_deploy_has_non_mutating_verify_only_mode() -> None:
    assert "CRIPTA_DASHBOARD_UI_VERIFY_ONLY" in UI_DEPLOY
    assert "DASHBOARD_UI_VERIFY_ONLY=PASS" in UI_DEPLOY
    verify_index = UI_DEPLOY.index("DASHBOARD_UI_VERIFY_ONLY=PASS")
    switch_index = UI_DEPLOY.index('mv -Tf "$next_ui" "$UI_ROOT/current"')
    assert verify_index < switch_index
