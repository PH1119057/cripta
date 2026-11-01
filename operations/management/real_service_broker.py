#!/usr/bin/env python3
"""Least-privilege local broker for bounded CRIPTA service starts.

The broker owns only OS-level START for a narrow allowlist. It has no DB,
Exchange, Strategy, OWNER ARM or mainnet-gate capability.
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
from pathlib import Path
from typing import Any

SOCKET_PATH = Path("/run/cripta-real-readiness/broker.sock")
INSTALLED_COMMIT = Path("/var/lib/cripta/release/INSTALLED_COMMIT")
RUNTIME_COMMIT = Path("/srv/cripta/runtime/current/INSTALLED_COMMIT")
MAX_REQUEST_BYTES = 16384
MAX_UNITS = 16
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ACCOUNT_UNIT_RE = re.compile(
    r"^cripta-(?:private-runtime|universal-entry-consumer|"
    r"lifecycle-account|account-position)@ta-[a-z0-9]{32}\.service$"
)
GLOBAL_UNITS = frozenset(
    {
        "cripta-private-runtime.service",
        "cripta-universal-entry-observer.service",
        "cripta-universal-entry-consumer.service",
        "cripta-universal-exit-consumer.service",
        "cripta-lifecycle-supervisor.service",
        "cripta-position-supervisor.service",
    }
)
ALLOWED_PRE_STATES = frozenset({"active", "inactive"})


class BrokerError(ValueError):
    """Stable protocol/authorization failure."""


def _is_allowed_unit(unit: str) -> bool:
    return unit in GLOBAL_UNITS or bool(ACCOUNT_UNIT_RE.fullmatch(unit))


def _read_commit(path: Path) -> str:
    value = path.read_text(encoding="ascii").strip().lower()
    if not SHA_RE.fullmatch(value):
        raise BrokerError("RELEASE_IDENTITY_INVALID")
    return value


def verify_release(requested: str) -> None:
    if not SHA_RE.fullmatch(requested):
        raise BrokerError("REQUEST_RELEASE_INVALID")
    installed = _read_commit(INSTALLED_COMMIT)
    runtime = _read_commit(RUNTIME_COMMIT)
    if requested != installed or requested != runtime:
        raise BrokerError("RELEASE_IDENTITY_MISMATCH")


def _systemctl_show(unit: str) -> dict[str, str]:
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            unit,
            "-p",
            "LoadState",
            "-p",
            "ActiveState",
            "-p",
            "FragmentPath",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        raise BrokerError(f"SERVICE_STATE_UNREADABLE:{unit}")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    if values.get("LoadState") != "loaded" or not values.get("FragmentPath"):
        raise BrokerError(f"SERVICE_NOT_LOADED:{unit}")
    return values


def _start_unit(unit: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["/usr/bin/systemctl", "start", unit],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:300]
        return False, detail or "systemctl start failed"
    state = _systemctl_show(unit).get("ActiveState", "")
    return state == "active", state or "unknown"


def handle_request(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"action", "request_id", "release_commit", "units"}:
        raise BrokerError("REQUEST_FIELDS_INVALID")
    if payload["action"] != "START":
        raise BrokerError("ACTION_NOT_ALLOWED")
    request_id = str(payload["request_id"])
    release_commit = str(payload["release_commit"]).lower()
    units = payload["units"]
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise BrokerError("REQUEST_ID_INVALID")
    if not isinstance(units, list) or not 1 <= len(units) <= MAX_UNITS:
        raise BrokerError("UNIT_COUNT_INVALID")
    normalized: list[str] = []
    for raw in units:
        unit = str(raw)
        if unit in normalized or not _is_allowed_unit(unit):
            raise BrokerError("UNIT_NOT_ALLOWED")
        normalized.append(unit)

    verify_release(release_commit)

    before: dict[str, str] = {}
    for unit in normalized:
        state = _systemctl_show(unit).get("ActiveState", "")
        if state not in ALLOWED_PRE_STATES:
            raise BrokerError(f"SERVICE_NOT_RECOVERABLE:{unit}:{state or 'unknown'}")
        before[unit] = state

    results: list[dict[str, object]] = []
    for unit in normalized:
        state = before[unit]
        if state == "active":
            results.append(
                {"unit": unit, "before": "active", "after": "active", "started": False}
            )
            continue
        ok, detail = _start_unit(unit)
        after = "active" if ok else _systemctl_show(unit).get("ActiveState", "unknown")
        results.append(
            {
                "unit": unit,
                "before": "inactive",
                "after": after,
                "started": ok,
                "detail": detail,
            }
        )
        if not ok:
            return {
                "ok": False,
                "request_id": request_id,
                "release_commit": release_commit,
                "error": f"SERVICE_START_FAILED:{unit}",
                "units": results,
            }

    return {
        "ok": True,
        "request_id": request_id,
        "release_commit": release_commit,
        "units": results,
    }


def _peer_uid(connection: socket.socket) -> int:
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


def _reply(connection: socket.socket, payload: dict[str, Any]) -> None:
    connection.sendall(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )


def _serve_client(connection: socket.socket, allowed_uid: int) -> None:
    if _peer_uid(connection) != allowed_uid:
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
            raise BrokerError("REQUEST_NOT_OBJECT")
        _reply(connection, handle_request(payload))
    except (BrokerError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        _reply(connection, {"ok": False, "error": str(exc)})


def _prepare_socket(path: Path) -> socket.socket:
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != 0:
            raise BrokerError("SOCKET_PATH_UNSAFE")
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chown(path, 0, grp.getgrnam("cripta").gr_gid)
    os.chmod(path, 0o660)
    server.listen(8)
    return server


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("broker must run as root")
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
                _serve_client(connection, allowed_uid)
    finally:
        try:
            server.close()
        finally:
            if SOCKET_PATH.exists() and stat.S_ISSOCK(SOCKET_PATH.lstat().st_mode):
                SOCKET_PATH.unlink()


if __name__ == "__main__":
    main()
