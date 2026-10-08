"""Installer must never arbitrate or mutate trading inventory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"


def test_install_does_not_require_flat_exchange_or_empty_command_queue() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    forbidden = (
        'die "Exchange hot position exists"',
        'die "pending Exchange order exists"',
        'die "open/reconciliation StrategyPosition exists"',
        'die "pending runtime trade command exists"',
        'die "real Strategy execution permissions must be zero before deploy"',
        'die "mainnet gate must be disarmed before deploy"',
    )
    for token in forbidden:
        assert token not in script


def test_installer_does_not_mutate_trading_gate_or_live_arm_sessions() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    assert "UPDATE control.live_arm_sessions" not in script
    assert "UPDATE control.execution_gates" not in script
    assert "UPDATE strategy_entry.execution_permissions" not in script
    assert 'TRADING_CONTROL_UNCHANGED=PASS' in script
    assert 'TRADING_CONTROL_CHANGED_EXTERNALLY=WARNING' in script
    assert 'gate_after" == "$pre_gate' in script
    assert 'sessions_after" == "$pre_sessions' in script


def test_post_release_observes_exchange_to_database_reconciliation() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    assert 'POST_DEPLOY_RECONCILIATION_OWNER=cripta-private-runtime.service' in script
    assert 'FROM runtime.reconciliation_runs WHERE ok=1' in script
    assert 'FROM runtime.reconciliation_runs WHERE ok=true' not in script
    assert 'POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION=PASS' in script
    assert 'POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION=NOT_VERIFIED' in script
    assert '/v5/order/create' not in script
    assert '/v5/order/cancel' not in script
