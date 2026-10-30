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
