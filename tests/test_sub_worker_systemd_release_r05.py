"""Offline, fail-closed SUB service provision contract. No server or Bybit access."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bybit_workbench.sub_worker_scope import require_exact_sub_instance

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research/server/connectivity"))
sys.path.insert(0, str(ROOT / "operations/connectivity"))

import private_runtime  # noqa: E402

PRIVATE = ROOT / "operations/systemd/cripta-private-runtime@.service"
ENTRY = ROOT / "operations/systemd/cripta-universal-entry-consumer@.service"
EXISTING_MAIN = ROOT / "research/server/connectivity/cripta-private-runtime.service"
EXISTING_EXIT = ROOT / "operations/systemd/cripta-universal-exit-consumer.service"


@pytest.mark.parametrize(
    ("kind", "account", "argv"),
    [
        ("SUB", "ta-one", ()),
        ("SUB", "ta-one", ("--sub-account=ta-two",)),
        ("MAIN", "ta-one", ("--sub-account=ta-one",)),
        ("SUB", "", ("--sub-account=",)),
        ("SUB", "ta-one", ("--sub-account=ta-one", "--extra")),
        ("SHARED", "ta-one", ()),
    ],
)
def test_wrong_worker_instance_cannot_inherit_main_authority(
    kind: str, account: str, argv: tuple[str, ...],
) -> None:
    with pytest.raises(RuntimeError, match="PRIVATE_SUB_INSTANCE_IDENTITY_MISMATCH"):
        require_exact_sub_instance(
            account_kind=kind, trading_account_id=account, argv=argv
        )


def test_existing_main_remains_compatible_without_sub_argument() -> None:
    require_exact_sub_instance(account_kind="MAIN", trading_account_id="ta-main", argv=())
    require_exact_sub_instance(
        account_kind="SUB",
        trading_account_id="ta-one",
        argv=("--sub-account=ta-one",),
    )


def test_sub_private_service_requires_exact_credential_and_scoped_write_root() -> None:
    unit = PRIVATE.read_text(encoding="utf-8")
    assert "[Install]\n" not in unit
    assert "User=cripta" in unit and "Group=cripta" in unit
    assert "Environment=CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND=SUB" in unit
    assert "Environment=CRIPTA_BOT_INSTANCE_ID=private-sub-%i" in unit
    assert "Environment=CRIPTA_BYBIT_CREDENTIAL_NAME=bybit-sub-%i" in unit
    assert "Environment=CRIPTA_PRIVATE_STATUS_DIR=/var/lib/cripta/private_runtime/sub-%i" in unit
    assert "EnvironmentFile=/etc/cripta/private-runtime-%i.env" in unit
    assert (
        "LoadCredentialEncrypted=bybit-sub-%i:/etc/cripta/credentials/bybit-sub-%i.cred"
        in unit
    )
    assert "private_runtime.py --sub-account=%i" in unit
    assert "StartLimitBurst=3" in unit
    assert "StartLimitIntervalSec=300" in unit
    assert "ProtectSystem=strict" in unit and "NoNewPrivileges=true" in unit
    assert "ReadWritePaths=/var/lib/cripta/private_runtime/sub-%i" in unit
    assert "ReadWritePaths=/var/lib/cripta/private_runtime\n" not in unit
    assert "bybit-mainnet.cred" not in unit


def test_sub_entry_service_is_inert_with_no_private_credential_or_exit_clone() -> None:
    unit = ENTRY.read_text(encoding="utf-8")
    assert "[Install]\n" not in unit
    assert "Environment=CRIPTA_UNIVERSAL_ENTRY_MAINNET_CONSUMER=DISABLED" in unit
    assert "universal_entry_consumer.py --sub-account=%i" in unit
    assert "EnvironmentFile=/etc/cripta/private-runtime-%i.env" in unit
    assert "BindsTo=cripta-private-runtime@%i.service" in unit
    assert (
        "After=network-online.target postgresql.service cripta-private-runtime@%i.service"
        in unit
    )
    assert "LoadCredential" not in unit
    assert "ReadWritePaths=" not in unit
    assert "NoNewPrivileges=true" in unit
    assert "cripta-universal-exit-consumer@%i" not in unit
    # Existing Exit is a single separately gated account-aware consumer.
    exit_unit = EXISTING_EXIT.read_text(encoding="utf-8")
    assert "operations/connectivity/universal_exit_consumer.py" in exit_unit
    assert "CRIPTA_UNIVERSAL_EXIT_MAINNET_CONSUMER=DISABLED" in exit_unit


def test_existing_main_private_service_is_not_changed_into_sub_template() -> None:
    current = EXISTING_MAIN.read_text(encoding="utf-8")
    assert "bybit-mainnet.cred" in current
    assert "private_runtime.py\n" in current
    assert "--sub-account=" not in current


def test_sub_startup_rejects_missing_status_dir_before_secret_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    account = "ta-one"
    monkeypatch.setattr(private_runtime, "DEFAULT_STATUS_DIR", tmp_path)
    monkeypatch.setattr(private_runtime, "TRADING_ACCOUNT_ID", account)
    monkeypatch.setattr(private_runtime.sys, "argv", [
        "private_runtime.py", "--sub-account=ta-one",
    ])
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    monkeypatch.setenv("CRIPTA_PRIVATE_STATUS_DIR", str(tmp_path / "sub-ta-one"))
    with pytest.raises(RuntimeError, match="SUB_PRIVATE_WORKER_STATUS_DIRECTORY_NOT_PREPARED"):
        private_runtime.main()


def test_sub_startup_rejects_cross_account_credential_before_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    account = "ta-one"
    sub_directory = tmp_path / f"sub-{account}"
    sub_directory.mkdir(mode=0o750)
    monkeypatch.setattr(private_runtime, "DEFAULT_STATUS_DIR", tmp_path)
    monkeypatch.setattr(private_runtime, "TRADING_ACCOUNT_ID", account)
    monkeypatch.setattr(private_runtime, "BOT_INSTANCE_ID", "private-sub-ta-one")
    monkeypatch.setattr(private_runtime.sys, "argv", [
        "private_runtime.py", "--sub-account=ta-one",
    ])
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    monkeypatch.setenv("CRIPTA_PRIVATE_STATUS_DIR", str(sub_directory))
    monkeypatch.setenv("CRIPTA_BYBIT_CREDENTIAL_NAME", "bybit-sub-ta-two")
    with pytest.raises(RuntimeError, match="SUB_PRIVATE_WORKER_CREDENTIAL_ACCOUNT_MISMATCH"):
        private_runtime.main()


def test_sub_startup_rejects_main_or_wrong_bot_identity_before_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    (tmp_path / "sub-ta-one").mkdir(mode=0o750)
    monkeypatch.setattr(private_runtime, "DEFAULT_STATUS_DIR", tmp_path)
    monkeypatch.setattr(private_runtime, "TRADING_ACCOUNT_ID", "ta-one")
    monkeypatch.setattr(private_runtime, "BOT_INSTANCE_ID", "m3-mainnet-primary")
    monkeypatch.setattr(
        private_runtime.sys,
        "argv",
        ["private_runtime.py", "--sub-account=ta-one"],
    )
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    monkeypatch.setenv("CRIPTA_PRIVATE_STATUS_DIR", str(tmp_path / "sub-ta-one"))
    monkeypatch.setenv("CRIPTA_BYBIT_CREDENTIAL_NAME", "bybit-sub-ta-one")
    with pytest.raises(ValueError, match="SUB_PRIVATE_WORKER_ISOLATION_NOT_CONFIGURED"):
        private_runtime.main()
    monkeypatch.setattr(private_runtime, "BOT_INSTANCE_ID", "private-sub-ta-two")
    with pytest.raises(RuntimeError, match="SUB_PRIVATE_WORKER_BOT_INSTANCE_ACCOUNT_MISMATCH"):
        private_runtime.main()


def test_entry_consumer_checks_cli_scope_even_while_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import operations.connectivity.universal_entry_consumer as consumer

    source = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text()
    main = source.split("def main() -> int:", 1)[1]
    assert main.index("require_exact_sub_instance(") < main.index(
        'CONSUMER_ARM != "ENABLED"'
    )
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    monkeypatch.setattr(consumer, "TRADING_ACCOUNT_ID", "ta-two")
    monkeypatch.setattr(consumer.sys, "argv", [
        "universal_entry_consumer.py", "--sub-account=ta-one",
    ])
    with pytest.raises(RuntimeError, match="PRIVATE_SUB_INSTANCE_IDENTITY_MISMATCH"):
        consumer.main()


def test_release_installs_sub_templates_and_account_schema_without_auto_start() -> None:
    installer = (ROOT / "operations/infrastructure/install_verified_release.sh").read_text()
    units = installer.split("unit_specs=(", 1)[1].split("\n)", 1)[0]
    managed = installer.split("managed_services=(", 1)[1].split("\n)", 1)[0]
    migrations = installer.split("migration_files=(", 1)[1].split("\n)", 1)[0]

    for template in (
        "cripta-private-runtime@.service",
        "cripta-universal-entry-consumer@.service",
    ):
        assert f"|{template}|runtime" in units
        assert template not in managed
    first = "operations/sql/20261024_account_scoped_live_arm.sql"
    second = "operations/sql/20261025_account_entry_safety_blocks.sql"
    assert first in migrations and second in migrations
    assert migrations.index(first) < migrations.index(second)
    fanout = "operations/sql/20261025_account_attempt_fanout.sql"
    assert fanout in migrations
    assert migrations.index(second) < migrations.index(fanout)
    # A previously running SUB participates in the same release identity
    # cutover; an absent SUB is never auto-started by a new release.
    assert "systemctl list-units --all --type=service" in installer
    assert 'managed_services+=("$unit")' in installer
    # The two templates have no [Install] section and no running instance
    # is introduced by the installer or by this offline test.
    assert "[Install]\n" not in PRIVATE.read_text(encoding="utf-8")
    assert "[Install]\n" not in ENTRY.read_text(encoding="utf-8")
