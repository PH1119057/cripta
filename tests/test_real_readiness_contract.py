from pathlib import Path
import shutil
import subprocess

import pytest

from bybit_workbench.real_service_profiles import p90_real_v3_service_specs
from bybit_workbench.real_service_readiness import (
    ServiceSnapshot,
    UnitState,
    plan_service_starts,
)


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
SUB = "ta-" + "1" * 32
CONNECTOR = "ac-" + "2" * 32


def test_p90_profile_is_first_consumer_of_generic_service_contract():
    specs = p90_real_v3_service_specs(sub_account_id=SUB)
    names = {item.unit for item in specs}
    assert len(specs) == 10
    assert "cripta-universal-exit-consumer.service" in names
    assert f"cripta-lifecycle-account@{SUB}.service" in names
    assert f"cripta-account-position@{SUB}.service" in names
    assert "cripta-dashboard.service" not in names

    snapshots = {
        item.unit: ServiceSnapshot(item.unit, UnitState.INACTIVE, SHA, True)
        for item in specs
    }
    result = plan_service_starts(
        release_commit=SHA,
        trading_account_id=SUB,
        account_connector_id=CONNECTOR,
        verified_specs=specs,
        snapshots=snapshots,
    )
    assert result.may_start
    assert set(result.starts) == names
    assert result.starts.index("cripta-private-runtime.service") < result.starts.index(
        "cripta-universal-entry-consumer.service"
    )
    assert result.starts.index(f"cripta-private-runtime@{SUB}.service") < result.starts.index(
        f"cripta-lifecycle-account@{SUB}.service"
    )


def test_broker_systemd_unit_is_root_but_narrowly_sandboxed():
    unit = (
        ROOT / "operations/systemd/cripta-real-service-broker.service"
    ).read_text(encoding="utf-8")
    assert "User=root" in unit
    assert "Group=cripta" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "ReadWritePaths=/run/cripta-real-readiness" in unit
    assert "ProtectSystem=strict" in unit
    assert "InaccessiblePaths=/etc/cripta/credentials" in unit
    assert (
        "ExecStart=/usr/bin/python3 "
        "/usr/local/libexec/cripta-real-service-broker/real_service_broker.py"
        in unit
    )
    assert "ReadOnlyPaths=/usr/local/libexec/cripta-real-service-broker" in unit
    assert "Environment=PYTHONNOUSERSITE=1" in unit
    assert "AF_INET" not in unit
    assert "sudo" not in unit


def test_verified_release_installs_broker_and_durable_state_but_does_not_arm():
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "cripta-real-service-broker.service" in installer
    assert "20261102_real_readiness_operations.sql" in installer
    assert "systemctl enable cripta-real-service-broker.service" in installer
    assert "systemctl start cripta-real-service-broker.service" in installer
    assert "REAL_SERVICE_BROKER=ACTIVE" in installer
    assert "REAL_SERVICE_BROKER_SOURCE_PREFLIGHT=PASS" in installer
    assert "/usr/local/libexec/cripta-real-service-broker" in installer
    assert '"$broker_staged"' in installer


def test_readiness_migration_has_no_trading_effect_writes():
    migration = (
        ROOT / "operations/sql/20261102_real_readiness_operations.sql"
    ).read_text(encoding="utf-8")
    assert "control.real_readiness_operations" in migration
    assert "GRANT SELECT,INSERT,UPDATE" in migration
    for forbidden in (
        "UPDATE control.execution_gates",
        "INSERT INTO control.live_arm_sessions",
        "INSERT INTO control.account_live_arm_sessions",
        "UPDATE strategy_entry.execution_permissions",
    ):
        assert forbidden not in migration


def test_dashboard_contains_persistent_friendly_preparation_states():
    ui = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "Подготовка P90 REAL v3…" in ui
    assert "Повторить подготовку" in ui
    assert "проверяем обязательные службы" in ui
    assert "запускаем недостающие службы" in ui
    assert "подготовка заблокирована" in ui
    assert "mainnet gate CLOSED" in ui

def test_verified_release_shell_syntax() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available on this runner")
    subprocess.run(
        [bash, "-n", str(ROOT / "operations/infrastructure/install_verified_release.sh")],
        check=True,
        capture_output=True,
        text=True,
    )


def test_dashboard_inline_javascript_parses() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available on this runner")
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    assert html.count("<script>") == 1
    assert html.count("</script>") == 1
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    probe = ROOT / ".pytest-dashboard-inline-syntax.js"
    try:
        probe.write_text(script, encoding="utf-8")
        subprocess.run(
            [node, "--check", str(probe)],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        probe.unlink(missing_ok=True)

