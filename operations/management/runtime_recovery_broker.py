#!/usr/bin/env python3
"""Least-privilege local broker for bounded CRIPTA runtime recovery.

This broker owns only OS-level START/RESTART for the exact current P90 service
allowlist. It has no database, Exchange, Strategy, OWNER ARM or gate capability.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import signal
import socket
import stat
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

SOCKET_PATH = Path("/run/cripta-runtime-recovery/broker.sock")
INSTALLED_COMMIT = Path("/var/lib/cripta/release/INSTALLED_COMMIT")
RUNTIME_COMMIT = Path("/srv/cripta/runtime/current/INSTALLED_COMMIT")
MAX_REQUEST_BYTES = 8192
MAX_RECOVERY_SECONDS = 45.0
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SUB_ACCOUNT_ID = "ta-853ac5fcd58185697396d33cd57459fa"
ACCOUNT_UNIT_RE = re.compile(
    r"^cripta-(?:private-runtime|universal-entry-consumer|"
    r"lifecycle-account|account-position)@(ta-[a-z0-9]{32})\.service$"
)
GLOBAL_UNITS = frozenset(
    {
        "cripta-private-runtime.service",
        "cripta-universal-entry-observer.service",
        "cripta-universal-entry-consumer.service",
        "cripta-lifecycle-supervisor.service",
        "cripta-position-supervisor.service",
    }
)
ALLOWED_ACCOUNT_IDS = frozenset({SUB_ACCOUNT_ID})
ALLOWED_ACTIONS = frozenset({"START", "RESTART"})
ALLOWED_EXPECTED_STATES = frozenset({"active", "inactive", "failed"})
RECOVERY_SUPERVISOR_CGROUP = (
    "/system.slice/cripta-runtime-recovery-supervisor.service"
)


class RecoveryBrokerError(ValueError):
    """Stable protocol/authorization failure."""


def _is_allowed_unit(unit: str) -> bool:
    if unit in GLOBAL_UNITS:
        return True
    match = ACCOUNT_UNIT_RE.fullmatch(unit)
    return bool(match and match.group(1) in ALLOWED_ACCOUNT_IDS)


def _requires_reconciliation(unit: str) -> bool:
    return unit == "cripta-private-runtime.service" or unit.startswith(
        "cripta-private-runtime@"
    )


def _read_commit(path: Path) -> str:
    value = path.read_text(encoding="ascii").strip().lower()
    if not SHA_RE.fullmatch(value):
        raise RecoveryBrokerError("RELEASE_IDENTITY_INVALID")
    return value


def verify_release(requested: str) -> None:
    if not SHA_RE.fullmatch(requested):
        raise RecoveryBrokerError("REQUEST_RELEASE_INVALID")
    installed = _read_commit(INSTALLED_COMMIT)
    runtime = _read_commit(RUNTIME_COMMIT)
    if requested != installed or requested != runtime:
        raise RecoveryBrokerError("RELEASE_IDENTITY_MISMATCH")


def _systemctl_show(unit: str, *, deadline: float | None = None) -> dict[str, str]:
    timeout = 8.0
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecoveryBrokerError("RECOVERY_DEADLINE_EXCEEDED")
        timeout = min(timeout, remaining)
    try:
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                unit,
                "-p",
                "Id",
                "-p",
                "LoadState",
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "FragmentPath",
                "-p",
                "MainPID",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecoveryBrokerError(f"SERVICE_STATE_TIMEOUT:{unit}") from exc
    if result.returncode != 0:
        raise RecoveryBrokerError(f"SERVICE_STATE_UNREADABLE:{unit}")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    if (
        values.get("Id") != unit
        or values.get("LoadState") != "loaded"
        or not values.get("FragmentPath")
    ):
        raise RecoveryBrokerError(f"SERVICE_IDENTITY_INVALID:{unit}")
    try:
        pid = int(values.get("MainPID", ""))
    except ValueError as exc:
        raise RecoveryBrokerError(f"SERVICE_PID_INVALID:{unit}") from exc
    if pid < 0:
        raise RecoveryBrokerError(f"SERVICE_PID_INVALID:{unit}")
    values["MainPID"] = str(pid)
    return values


def _validate_fresh_state(
    *,
    action: str,
    unit: str,
    expected_active_state: str,
    expected_main_pid: int,
    current: dict[str, str],
) -> None:
    state = current.get("ActiveState", "")
    current_pid = int(current.get("MainPID", "-1"))
    if state != expected_active_state or current_pid != expected_main_pid:
        raise RecoveryBrokerError("RECOVERY_PRECONDITION_STALE")

    if action == "START":
        if state != "inactive" or current_pid != 0:
            raise RecoveryBrokerError("START_PRECONDITION_INVALID")
        return

    if action != "RESTART":
        raise RecoveryBrokerError("ACTION_NOT_ALLOWED")
    if state not in {"active", "failed"}:
        raise RecoveryBrokerError("RESTART_PRECONDITION_INVALID")
    if state == "active" and current_pid <= 0:
        raise RecoveryBrokerError("RESTART_PID_REQUIRED")
    if state == "failed" and current_pid != 0:
        raise RecoveryBrokerError("FAILED_SERVICE_PID_NOT_ZERO")


def _run_action(
    *,
    action: str,
    unit: str,
    deadline: float,
) -> tuple[bool, str, dict[str, str]]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False, "RECOVERY_DEADLINE_EXCEEDED", {}
    verb = "start" if action == "START" else "restart"
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", verb, unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=min(20.0, remaining),
        )
    except subprocess.TimeoutExpired:
        return False, "systemctl timed out", {}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:300]
        return False, detail or f"systemctl {verb} failed", {}

    last: dict[str, str] = {}
    while time.monotonic() <= deadline:
        try:
            last = _systemctl_show(unit, deadline=deadline)
        except RecoveryBrokerError as exc:
            return False, str(exc), {}
        state = last.get("ActiveState", "")
        if state == "active":
            return True, "active", last
        if state in {"failed", "deactivating"}:
            return False, state, last
        time.sleep(0.2)
    return False, "RECOVERY_DEADLINE_EXCEEDED", last


def handle_request(payload: dict[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "action",
        "request_id",
        "release_commit",
        "unit",
        "expected_active_state",
        "expected_main_pid",
    }
    if set(payload) != expected_fields:
        raise RecoveryBrokerError("REQUEST_FIELDS_INVALID")

    action = str(payload["action"])
    request_id = str(payload["request_id"])
    release_commit = str(payload["release_commit"]).lower()
    unit = str(payload["unit"])
    expected_state = str(payload["expected_active_state"])
    expected_pid_raw = payload["expected_main_pid"]

    if action not in ALLOWED_ACTIONS:
        raise RecoveryBrokerError("ACTION_NOT_ALLOWED")
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise RecoveryBrokerError("REQUEST_ID_INVALID")
    if not _is_allowed_unit(unit):
        raise RecoveryBrokerError("UNIT_NOT_ALLOWED")
    if expected_state not in ALLOWED_EXPECTED_STATES:
        raise RecoveryBrokerError("EXPECTED_STATE_INVALID")
    if (
        isinstance(expected_pid_raw, bool)
        or not isinstance(expected_pid_raw, int)
        or expected_pid_raw < 0
    ):
        raise RecoveryBrokerError("EXPECTED_PID_INVALID")

    verify_release(release_commit)

    deadline = time.monotonic() + MAX_RECOVERY_SECONDS
    current = _systemctl_show(unit, deadline=deadline)
    _validate_fresh_state(
        action=action,
        unit=unit,
        expected_active_state=expected_state,
        expected_main_pid=expected_pid_raw,
        current=current,
    )

    ok, detail, after = _run_action(
        action=action,
        unit=unit,
        deadline=deadline,
    )
    before_pid = int(current["MainPID"])
    after_pid = int(after.get("MainPID", "0")) if after else 0
    result = {
        "ok": ok,
        "request_id": request_id,
        "release_commit": release_commit,
        "action": action,
        "unit": unit,
        "before": {
            "active_state": current.get("ActiveState", ""),
            "sub_state": current.get("SubState", ""),
            "main_pid": before_pid,
        },
        "after": {
            "active_state": after.get("ActiveState", "unknown") if after else "unknown",
            "sub_state": after.get("SubState", "") if after else "",
            "main_pid": after_pid,
        },
        "reconciliation_required": _requires_reconciliation(unit),
        "detail": detail,
    }
    if not ok:
        result["error"] = f"SERVICE_RECOVERY_FAILED:{unit}"
    return result


def _peer_credentials(connection: socket.socket) -> tuple[int, int]:
    raw = connection.getsockopt(
        socket.SOL_SOCKET,
        socket.SO_PEERCRED,
        struct.calcsize("3i"),
    )
    pid, uid, _gid = struct.unpack("3i", raw)
    return pid, uid


def _peer_is_recovery_supervisor(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> bool:
    if pid <= 0:
        return False
    try:
        lines = (proc_root / str(pid) / "cgroup").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError:
        return False
    return any(
        line == "0::" + RECOVERY_SUPERVISOR_CGROUP
        or line.endswith(":" + RECOVERY_SUPERVISOR_CGROUP)
        for line in lines
    )


def _reply(connection: socket.socket, payload: dict[str, Any]) -> None:
    connection.sendall(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    )


def _serve_client(connection: socket.socket, allowed_uid: int) -> None:
    connection.settimeout(5.0)
    pid, uid = _peer_credentials(connection)
    if uid != allowed_uid or not _peer_is_recovery_supervisor(pid):
        _reply(connection, {"ok": False, "error": "PEER_NOT_ALLOWED"})
        return

    data = b""
    while b"\n" not in data and len(data) <= MAX_REQUEST_BYTES:
        chunk = connection.recv(4096)
        if not chunk:
            break
        data += chunk
    if len(data) > MAX_REQUEST_BYTES:
        _reply(connection, {"ok": False, "error": "REQUEST_TOO_LARGE"})
        return

    try:
        line = data.split(b"\n", 1)[0]
        payload = json.loads(line.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RecoveryBrokerError("REQUEST_NOT_OBJECT")
        _reply(connection, handle_request(payload))
    except (
        RecoveryBrokerError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        _reply(connection, {"ok": False, "error": str(exc)})


def _prepare_socket(path: Path) -> socket.socket:
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != 0:
            raise RecoveryBrokerError("SOCKET_PATH_UNSAFE")
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chown(path, 0, grp.getgrnam("cripta").gr_gid)
    os.chmod(path, 0o660)
    server.listen(8)
    return server


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("recovery broker must run as root")
    allowed_uid = pwd.getpwnam("cripta").pw_uid
    server = _prepare_socket(SOCKET_PATH)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        server.close()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                connection, _ = server.accept()
            except OSError:
                if stopping:
                    break
                raise
            with connection:
                try:
                    _serve_client(connection, allowed_uid)
                except OSError:
                    # Peer timeout/disconnect is request-local.
                    continue
    finally:
        try:
            server.close()
        finally:
            if SOCKET_PATH.exists() and stat.S_ISSOCK(SOCKET_PATH.lstat().st_mode):
                SOCKET_PATH.unlink()


if __name__ == "__main__":
    main()
