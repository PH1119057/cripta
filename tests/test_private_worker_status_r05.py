from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bybit_workbench.private_worker_status import (
    assert_sub_worker_isolation,
    private_worker_status_path,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/connectivity"))

import private_runtime  # noqa: E402


def test_existing_main_status_identity_and_dashboard_reader_are_preserved() -> None:
    path = private_worker_status_path(
        trading_account_id="ta-primary", account_kind="MAIN"
    )
    assert path == Path("/var/lib/cripta/private_runtime/status.json")
    assert path.with_suffix(".tmp") == Path("/var/lib/cripta/private_runtime/status.tmp")


def test_distinct_subaccounts_cannot_clobber_same_status_or_atomic_temp_file() -> None:
    first = private_worker_status_path(
        trading_account_id="ta-one", account_kind="SUB"
    )
    second = private_worker_status_path(
        trading_account_id="ta-two", account_kind="SUB"
    )
    assert first != second
    assert first.with_suffix(".tmp") != second.with_suffix(".tmp")
    assert first.name == "status-ta-one.json"
    assert second.name == "status-ta-two.json"


@pytest.mark.parametrize("value", ["", ".", "../escape", "a/b", " a ", "ñ"])
def test_untrusted_account_id_cannot_escape_status_directory(value: str) -> None:
    with pytest.raises(ValueError, match="PRIVATE_WORKER_TRADING_ACCOUNT_ID_INVALID"):
        private_worker_status_path(trading_account_id=value, account_kind="SUB")


def test_unapproved_private_account_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="PRIVATE_WORKER_ACCOUNT_KIND_INVALID"):
        private_worker_status_path(trading_account_id="ta-one", account_kind="SHARED")


@pytest.mark.parametrize(
    ("bot", "credential"),
    [
        ("", "bybit-sub-one"),
        ("m3-mainnet-primary", "bybit-sub-one"),
        ("sub-worker", ""),
        ("sub-worker", "bybit-mainnet"),
    ],
)
def test_sub_account_cannot_inherit_main_worker_or_credentials(
    bot: str, credential: str
) -> None:
    with pytest.raises(ValueError, match="SUB_PRIVATE_WORKER_ISOLATION_NOT_CONFIGURED"):
        assert_sub_worker_isolation(
            account_kind="SUB", bot_instance_id=bot, credential_name=credential
        )


def test_sub_worker_must_be_independently_named() -> None:
    assert_sub_worker_isolation(
        account_kind="SUB",
        bot_instance_id="sub-worker-1",
        credential_name="bybit-sub-1",
    )


def test_main_worker_remains_compatible_and_exit_is_not_gated_by_new_entry_proof() -> None:
    assert_sub_worker_isolation(
        account_kind="MAIN",
        bot_instance_id="m3-mainnet-primary",
        credential_name="bybit-mainnet",
    )
    source = (ROOT / "operations/connectivity/private_runtime.py").read_text()
    entry_path = source[
        source.index("def execute_command("):source.index("def command_worker_loop(")
    ]
    assert 'if kind == "entry":' in entry_path
    assert entry_path.index("account_entry_safety_ready(") < entry_path.index(
        "authenticated_entry_connector_gate("
    )
    assert "authenticated_entry_connector_gate(" in entry_path
    assert "assert_sub_worker_isolation(" in source
    assert "STATUS = private_worker_status_path(" in source


def test_sub_worker_scope_is_explicit_before_private_credential_or_database_read() -> None:
    source = (ROOT / "operations/connectivity/private_runtime.py").read_text()
    section = source.split("def main() -> None:", 1)[1]
    assert 'os.environ.get("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "MAIN")' in section
    assert 'if configured_kind not in {"MAIN", "SUB"}' in section
    assert section.index("STATUS = private_worker_status_path(") < section.index(
        'credentials = json.loads(credential_path.read_text'
    )
    assert section.index("assert_sub_worker_isolation(") < section.index(
        'credentials = json.loads(credential_path.read_text'
    )
    assert 'if account_kind != configured_kind:' in section
    assert 'if configured_kind == "MAIN":' in section
    assert section.index('if configured_kind == "MAIN":') < section.index(
        'disarm_new_entries('
    )
    assert "bootstrap.close()" in section


class _Bootstrap:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(("kind", "expected_disarm"), [("MAIN", 1), ("SUB", 0)])
def test_schema_startup_failure_only_disarms_existing_main_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    kind: str, expected_disarm: int,
) -> None:
    credential_name, account_id, bot_id = {
        "MAIN": ("bybit-mainnet", "test-main-account", "m3-mainnet-primary"),
        "SUB": ("bybit-sub-test-sub-account", "test-sub-account", "test-sub-worker"),
    }[kind]
    (tmp_path / credential_name).write_text(
        json.dumps({"api_key": "OFFLINE_FAKE", "api_secret": "OFFLINE_FAKE"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("CRIPTA_BYBIT_CREDENTIAL_NAME", credential_name)
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", kind)
    monkeypatch.setattr(private_runtime, "DEFAULT_STATUS_DIR", tmp_path)
    if kind == "SUB":
        sub_directory = tmp_path / f"sub-{account_id}"
        sub_directory.mkdir(mode=0o750)
        monkeypatch.setenv("CRIPTA_PRIVATE_STATUS_DIR", str(sub_directory))
    # main() rebinds process globals; restore them when the test finishes.
    monkeypatch.setattr(private_runtime, "STATUS", private_runtime.STATUS)
    monkeypatch.setattr(
        private_runtime, "MOUNTED_CREDENTIAL_REF", private_runtime.MOUNTED_CREDENTIAL_REF
    )
    monkeypatch.setattr(
        private_runtime, "TRADING_ACCOUNT_ID",
        account_id,
    )
    monkeypatch.setattr(
        private_runtime, "BOT_INSTANCE_ID",
        bot_id,
    )
    monkeypatch.setattr(
        private_runtime, "private_worker_status_path",
        lambda **_kwargs: tmp_path / f"test-status-{kind}.json",
    )
    connection = _Bootstrap()
    disarms: list[str] = []
    statuses: list[str] = []
    monkeypatch.setattr(private_runtime, "db", lambda _name: connection)
    monkeypatch.setattr(
        private_runtime,
        "validate_runtime_schema_contract",
        lambda _connection: (_ for _ in ()).throw(RuntimeError("OFFLINE_SCHEMA_FAILURE")),
    )
    monkeypatch.setattr(
        private_runtime,
        "disarm_new_entries",
        lambda _connection, reason: disarms.append(reason),
    )
    monkeypatch.setattr(
        private_runtime,
        "atomic_status",
        lambda channel, _value: statuses.append(channel),
    )
    with pytest.raises(RuntimeError, match="OFFLINE_SCHEMA_FAILURE"):
        private_runtime.main()
    assert connection.closed
    assert len(disarms) in (expected_disarm,)
    assert statuses in (["schema"],)
    assert private_runtime.STATUS in (tmp_path / f"test-status-{kind}.json",)
