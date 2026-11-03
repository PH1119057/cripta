from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.real_service_profiles import p90_real_v3_service_specs

broker = pytest.importorskip(
    "operations.management.runtime_recovery_broker",
    reason="runtime recovery broker is a POSIX-only production component",
)

SUB = "ta-853ac5fcd58185697396d33cd57459fa"
RELEASE = "a" * 40


def request(
    unit: str = "cripta-universal-entry-observer.service",
    *,
    action: str = "RESTART",
    state: str = "active",
    pid: int = 1234,
) -> dict[str, object]:
    return {
        "action": action,
        "request_id": "recovery-1234",
        "release_commit": RELEASE,
        "unit": unit,
        "expected_active_state": state,
        "expected_main_pid": pid,
    }


def shown(
    unit: str,
    *,
    state: str = "active",
    pid: int = 1234,
) -> dict[str, str]:
    return {
        "Id": unit,
        "LoadState": "loaded",
        "ActiveState": state,
        "SubState": "running" if state == "active" else "dead",
        "FragmentPath": "/etc/systemd/system/" + unit,
        "MainPID": str(pid),
    }


def test_allowlist_matches_exact_p90_profile() -> None:
    expected = {
        item.unit for item in p90_real_v3_service_specs(sub_account_id=SUB)
    }
    actual = {
        *broker.GLOBAL_UNITS,
        *{
            f"cripta-private-runtime@{SUB}.service",
            f"cripta-universal-entry-consumer@{SUB}.service",
            f"cripta-lifecycle-account@{SUB}.service",
            f"cripta-account-position@{SUB}.service",
        },
    }
    assert actual == expected
    assert all(broker._is_allowed_unit(item) for item in expected)
    for denied in (
        "ssh.service",
        "cripta-dashboard.service",
        "cripta-mayak-v2.service",
        "cripta-dispatcher-v2.service",
        "cripta-safety-observer.service",
        "cripta-universal-exit-consumer.service",
        "cripta-private-runtime@ta-" + "1" * 32 + ".service",
    ):
        assert not broker._is_allowed_unit(denied)


def test_private_runtime_always_reports_reconciliation_required() -> None:
    assert broker._requires_reconciliation("cripta-private-runtime.service")
    assert broker._requires_reconciliation(
        f"cripta-private-runtime@{SUB}.service"
    )
    assert not broker._requires_reconciliation(
        "cripta-universal-entry-consumer.service"
    )


def test_peer_accepts_only_recovery_supervisor_cgroup(tmp_path: Path) -> None:
    pid = 4321
    cgroup = tmp_path / str(pid)
    cgroup.mkdir()
    (cgroup / "cgroup").write_text(
        "0::/system.slice/cripta-runtime-recovery-supervisor.service\n",
        encoding="utf-8",
    )
    assert broker._peer_is_recovery_supervisor(pid, proc_root=tmp_path)

    for denied in (
        "/system.slice/cripta-dashboard.service",
        "/system.slice/cripta-private-runtime.service",
    ):
        (cgroup / "cgroup").write_text("0::" + denied + "\n", encoding="utf-8")
        assert not broker._peer_is_recovery_supervisor(pid, proc_root=tmp_path)
    assert not broker._peer_is_recovery_supervisor(0, proc_root=tmp_path)


def test_request_rejects_extra_fields_before_systemctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def show(_unit: str, **_kwargs: object) -> dict[str, str]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(broker, "_systemctl_show", show)
    payload = request()
    payload["shell"] = "id"
    with pytest.raises(broker.RecoveryBrokerError, match="REQUEST_FIELDS_INVALID"):
        broker.handle_request(payload)
    assert not called


@pytest.mark.parametrize("action", ["STOP", "ENABLE", "DAEMON-RELOAD"])
def test_only_start_and_restart_actions_are_allowed(
    action: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(broker, "verify_release", lambda _commit: None)
    with pytest.raises(broker.RecoveryBrokerError, match="ACTION_NOT_ALLOWED"):
        broker.handle_request(request(action=action))


def test_unapproved_unit_rejected_before_release_or_systemctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_called = False

    def verify(_commit: str) -> None:
        nonlocal release_called
        release_called = True

    monkeypatch.setattr(broker, "verify_release", verify)
    with pytest.raises(broker.RecoveryBrokerError, match="UNIT_NOT_ALLOWED"):
        broker.handle_request(request("ssh.service"))
    assert not release_called


def test_release_identity_must_match_both_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = tmp_path / "installed"
    runtime = tmp_path / "runtime"
    installed.write_text(RELEASE + "\n", encoding="ascii")
    runtime.write_text("b" * 40 + "\n", encoding="ascii")
    monkeypatch.setattr(broker, "INSTALLED_COMMIT", installed)
    monkeypatch.setattr(broker, "RUNTIME_COMMIT", runtime)
    with pytest.raises(
        broker.RecoveryBrokerError,
        match="RELEASE_IDENTITY_MISMATCH",
    ):
        broker.verify_release(RELEASE)


@pytest.mark.parametrize(
    ("action", "expected_state", "expected_pid", "current_state", "current_pid"),
    [
        ("START", "inactive", 0, "active", 1234),
        ("RESTART", "active", 1234, "active", 5678),
        ("RESTART", "failed", 0, "inactive", 0),
    ],
)
def test_stale_precondition_rejects_replayed_or_raced_request(
    action: str,
    expected_state: str,
    expected_pid: int,
    current_state: str,
    current_pid: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(broker, "verify_release", lambda _commit: None)
    monkeypatch.setattr(
        broker,
        "_systemctl_show",
        lambda unit, **_kwargs: shown(unit, state=current_state, pid=current_pid),
    )
    monkeypatch.setattr(
        broker,
        "_run_action",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale request must not mutate")
        ),
    )
    with pytest.raises(
        broker.RecoveryBrokerError,
        match="RECOVERY_PRECONDITION_STALE",
    ):
        broker.handle_request(
            request(
                action=action,
                state=expected_state,
                pid=expected_pid,
            )
        )


@pytest.mark.parametrize(
    ("action", "state", "pid", "error"),
    [
        ("START", "active", 1234, "START_PRECONDITION_INVALID"),
        ("RESTART", "inactive", 0, "RESTART_PRECONDITION_INVALID"),
        ("RESTART", "active", 0, "RESTART_PID_REQUIRED"),
        ("RESTART", "failed", 9, "FAILED_SERVICE_PID_NOT_ZERO"),
    ],
)
def test_action_state_matrix_is_fail_closed(
    action: str,
    state: str,
    pid: int,
    error: str,
) -> None:
    with pytest.raises(broker.RecoveryBrokerError, match=error):
        broker._validate_fresh_state(
            action=action,
            unit="cripta-universal-entry-observer.service",
            expected_active_state=state,
            expected_main_pid=pid,
            current=shown(
                "cripta-universal-entry-observer.service",
                state=state,
                pid=pid,
            ),
        )


@pytest.mark.parametrize(
    ("action", "verb"),
    [("START", "start"), ("RESTART", "restart")],
)
def test_mutation_uses_exact_systemctl_argv_without_shell(
    action: str,
    verb: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool]] = []
    unit = "cripta-universal-entry-observer.service"

    def run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, bool(kwargs.get("shell", False))))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(broker.subprocess, "run", run)
    monkeypatch.setattr(
        broker,
        "_systemctl_show",
        lambda _unit, **_kwargs: shown(unit, state="active", pid=5678),
    )
    ok, detail, after = broker._run_action(
        action=action,
        unit=unit,
        deadline=broker.time.monotonic() + 10,
    )
    assert ok is True
    assert detail == "active"
    assert after["MainPID"] == "5678"
    assert calls[0] == (["/usr/bin/systemctl", verb, unit], False)


def test_successful_active_restart_changes_pid_and_marks_private_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = "cripta-private-runtime.service"
    monkeypatch.setattr(broker, "verify_release", lambda _commit: None)
    monkeypatch.setattr(
        broker,
        "_systemctl_show",
        lambda _unit, **_kwargs: shown(unit, state="active", pid=1234),
    )
    monkeypatch.setattr(
        broker,
        "_run_action",
        lambda **_kwargs: (
            True,
            "active",
            shown(unit, state="active", pid=5678),
        ),
    )
    result = broker.handle_request(request(unit))
    assert result["ok"] is True
    assert result["reconciliation_required"] is True
    assert result["before"]["main_pid"] == 1234
    assert result["after"]["main_pid"] == 5678


def test_start_replay_fails_after_service_becomes_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = "cripta-universal-entry-observer.service"
    states = iter(
        [
            shown(unit, state="inactive", pid=0),
            shown(unit, state="active", pid=5678),
        ]
    )
    monkeypatch.setattr(broker, "verify_release", lambda _commit: None)
    monkeypatch.setattr(
        broker,
        "_systemctl_show",
        lambda _unit, **_kwargs: next(states),
    )
    monkeypatch.setattr(
        broker,
        "_run_action",
        lambda **_kwargs: (
            True,
            "active",
            shown(unit, state="active", pid=5678),
        ),
    )
    first = broker.handle_request(
        request(unit, action="START", state="inactive", pid=0)
    )
    assert first["ok"] is True
    with pytest.raises(
        broker.RecoveryBrokerError,
        match="RECOVERY_PRECONDITION_STALE",
    ):
        broker.handle_request(
            request(unit, action="START", state="inactive", pid=0)
        )


def test_unsafe_existing_socket_path_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broker.sock"
    path.write_text("not a socket", encoding="utf-8")
    with pytest.raises(broker.RecoveryBrokerError, match="SOCKET_PATH_UNSAFE"):
        broker._prepare_socket(path)


def test_source_is_not_wired_into_installer_yet() -> None:
    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "cripta-runtime-recovery-broker" not in installer
    assert "runtime_recovery_broker.py" not in installer


def test_unit_is_hardened_and_inert_until_explicit_install() -> None:
    unit = Path(
        "operations/systemd/cripta-runtime-recovery-broker.service"
    ).read_text(encoding="utf-8")
    assert "User=root" in unit
    assert "Group=cripta" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectControlGroups=yes" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "InaccessiblePaths=/etc/cripta/credentials" in unit
    assert "RuntimeDirectory=cripta-runtime-recovery" in unit
    assert "ReadWritePaths=/run/cripta-runtime-recovery" in unit


def test_broker_does_not_import_db_exchange_or_project_runtime() -> None:
    source = Path(
        "operations/management/runtime_recovery_broker.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in (
        "psycopg",
        "sqlalchemy",
        "pybit",
        "bybit",
        "runtime_recovery_policy",
        "strategy_entry",
        "execution_gates",
    ):
        assert forbidden not in source