"""Installer must re-load every trading execution service on application cutover."""
from pathlib import Path


def test_exit_consumer_is_in_installer_managed_service_lifecycle():
    installer = Path("operations/infrastructure/install_verified_release.sh").read_text()
    managed = installer.split("managed_services=(", 1)[1].split("\n)", 1)[0]
    units = installer.split("unit_specs=(", 1)[1].split("\n)", 1)[0]
    name = "cripta-universal-exit-consumer.service"
    assert name in units
    assert sum(line.strip() == name for line in managed.splitlines()) == 1
    assert 'systemctl stop "$service"' in installer
    assert 'systemctl start "$service"' in installer
    assert 'systemctl is-active --quiet "$service"' in installer


def test_installer_remains_independent_of_open_positions():
    installer = Path("operations/infrastructure/install_verified_release.sh").read_text()
    assert "Release installation is independent of all Exchange" in installer
    assert 'PRE_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION' not in installer
    assert "POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION" in installer
