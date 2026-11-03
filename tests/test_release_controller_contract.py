"""Static safety contract for the Release Controller implementation (#288)."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "operations/infrastructure/cripta-release-controller"
UNIT = ROOT / "operations/systemd/cripta-release-controller.service"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"


def test_controller_source_is_valid_python_and_has_no_cli_parser():
    source = CONTROLLER.read_text(encoding="utf-8")
    ast.parse(source)
    assert "argparse" not in source
    assert "len(sys.argv) != 1" in source
    assert "OWNER_ARM=UNCHANGED" in source
    assert "DASHBOARD_PURPOSE" in source
    assert "/usr/local/sbin/cripta-deploy-dashboard-ui" in source
    assert "CRIPTA_DASHBOARD_UI_VERIFY_ONLY=1" in source
    assert "INSTALLED_COMMIT_UNCHANGED" in source
    assert "TRADING_RUNTIME_CHANGED=NO" in source
    assert '"/usr/bin/systemd-run"' in source
    assert '"--property=User={user}"' in source
    assert '"--property=Group={user}"' in source
    assert '"--property=NoNewPrivileges=yes"' in source
    assert "/usr/bin/setpriv" not in source
    assert "/usr/sbin/runuser" not in source
    assert '"/usr/bin/systemctl", "start", "cripta-source-sync.service"' in source
    assert '"/usr/bin/systemd-run"' in source
    assert '"--wait"' in source
    assert '"--pipe"' in source
    assert '"--collect"' in source
    assert '"--property=TimeoutStartSec=1800"' in source


def test_controller_service_is_inert_and_sandboxed():
    unit = UNIT.read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "User=root" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/lib/cripta/release-controller" in unit
    assert "ExecStart=/usr/bin/python3 /usr/local/libexec/cripta-release-controller/" in unit
    assert "ReadWritePaths=/usr/local/libexec/cripta-release-controller" in unit


def test_installer_installs_controller_but_does_not_manage_or_start_it():
    installer = INSTALLER.read_text(encoding="utf-8")
    spec = (
        '"operations/systemd/cripta-release-controller.service|'
        'cripta-release-controller.service|runtime"'
    )
    assert spec in installer
    managed = installer.split("managed_services=(", 1)[1].split(")", 1)[0]
    assert "cripta-release-controller.service" not in managed
    assert "enable --now cripta-release-controller" not in installer
    assert "systemctl disable cripta-release-controller.service" in installer
    assert "/usr/local/libexec/cripta-release-controller" in installer
    assert "-o root -g root -m 0755" in installer


def test_dashboard_controller_mode_uses_existing_ui_deployer_not_full_installer():
    source = CONTROLLER.read_text(encoding="utf-8")
    dashboard_block = source.split('if purpose == DASHBOARD_PURPOSE:', 1)[1].split(
        '_deploy(target=target', 1
    )[0]
    assert "_deploy_dashboard(target=target, nonce=nonce)" in dashboard_block
    assert "CRIPTA_DASHBOARD_UI_VERIFY_ONLY=1" in source
    assert "DASHBOARD_DEPLOY_CHANGED_RUNTIME_IDENTITY" in source
    assert "OWNER_ARM=UNCHANGED" in source


def test_dashboard_controller_spawns_bounded_transient_units_for_privilege_boundary():
    source = CONTROLLER.read_text(encoding="utf-8")
    start = source.index("def _run_dashboard_unit(")
    end = source.index("\n\ndef _deploy_dashboard(", start)
    block = source[start:end]
    assert '"/usr/bin/systemd-run"' in block
    assert '"--wait"' in block
    assert '"--pipe"' in block
    assert '"--collect"' in block
    assert "cripta-controller-dashboard-{stage}-{unit_suffix}" in block
    assert '"--property=Type=oneshot"' in block
    assert '"--property=TimeoutStartSec=300"' in block
    assert "CRIPTA_DASHBOARD_UI_VERIFY_ONLY=1" in block
    assert "/usr/local/sbin/cripta-deploy-dashboard-ui" in block
    assert "runuser" not in block
