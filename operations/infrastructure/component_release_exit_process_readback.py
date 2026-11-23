"""Bounded READ-ONLY Exit process identity readback; NOT a runtime release gate.

#849 Phase2i. Caller supplies *untrusted* systemctl-show evidence and an
explicit /proc root; a higher-privilege, no-argument controller must collect
real OS evidence and recheck it under the shared installer lock. This code
does not invoke systemd, read DB, access Exchange, install software or mutate
processes/files. It never returns non-allowlisted environment variables.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PYTHON = re.compile(r"/usr/bin/python3\.(?:12|13)\Z")
_UNIT = "cripta-universal-exit-consumer.service"
_PARENT = "/srv/cripta/components/exit/releases/"
_ENTRY = "operations/connectivity/universal_exit_consumer.py"
_REQUIRED_SHOW = frozenset({
    "Id", "ActiveState", "SubState", "MainPID", "NRestarts",
    "NeedDaemonReload", "WorkingDirectory",
})
_REQUIRED_ENV = frozenset({
    "CRIPTA_RELEASE_COMMIT", "CRIPTA_RUNTIME_ROOT", "PYTHONPATH",
})
_LEGACY = "/srv/cripta/runtime/current"


def _deny(reason: str) -> None:
    raise ValueError("EXIT_PROCESS_" + reason)


def _show_fields(raw: str) -> dict[str, str]:
    if not isinstance(raw, str) or len(raw) > 16_384:
        _deny("SYSTEMD_SHOW_MALFORMED")
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or "=" not in line:
            _deny("SYSTEMD_SHOW_MALFORMED")
        key, value = line.split("=", 1)
        if key not in _REQUIRED_SHOW or key in fields:
            _deny("SYSTEMD_SHOW_MALFORMED")
        fields[key] = value
    if set(fields) != _REQUIRED_SHOW:
        _deny("SYSTEMD_SHOW_MISSING_FIELDS")
    return fields


def _stat_start_ticks(raw: bytes, pid: int) -> int:
    if len(raw) > 8192:
        _deny("PROC_STAT_OVERSIZE")
    try:
        decoded = raw.decode("ascii")
        prefix, fields = decoded.rsplit(") ", 1)
        if not prefix.startswith(str(pid) + " ("):
            _deny("PROC_PID_CHANGED")
        bits = fields.split()
        if len(bits) < 20 or not bits[19].isdigit():
            _deny("PROC_START_TICKS_INVALID")
        start_ticks = int(bits[19])
        if start_ticks <= 0:
            _deny("PROC_START_TICKS_INVALID")
        return start_ticks
    except (UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("EXIT_PROCESS_"):
            raise
        raise ValueError("EXIT_PROCESS_PROC_STAT_INVALID") from exc


def _extract_env(raw: bytes) -> dict[str, str]:
    if len(raw) > 1024 * 1024:
        _deny("PROC_ENVIRONMENT_OVERSIZE")
    seen: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        key, sep, value = entry.partition(b"=")
        if not sep:
            _deny("PROC_ENVIRONMENT_MALFORMED")
        # Other environment values may be sensitive and are NEVER decoded,
        # stored, logged or returned by this module.
        if key not in {a.encode("ascii") for a in _REQUIRED_ENV}:
            continue
        try:
            decoded_key = key.decode("ascii")
            decoded_value = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("EXIT_PROCESS_IDENTITY_ENV_INVALID") from exc
        if decoded_key in seen:
            _deny("PROC_IDENTITY_ENV_DUPLICATE")
        seen[decoded_key] = decoded_value
    if set(seen) != _REQUIRED_ENV:
        _deny("PROC_IDENTITY_ENV_MISSING")
    return seen


def collect_exit_process_readback(
    *,
    systemd_show: str,
    expected_commit: str,
    proc_parent: Path = Path("/proc"),
) -> dict[str, object]:
    """Validate consistent observation; never infer actual authorization.

    For `cmdline`, use the process's argv path (venv python), not the
    kernel /proc/PID/exe realpath, which legitimately resolves to OS Python.
    Synthetic proc roots are test fixtures, not evidence of live behavior.
    """
    if not isinstance(expected_commit, str) or not _SHA.fullmatch(
        expected_commit
    ):
        _deny("SOURCE_COMMIT_INVALID")
    if not isinstance(proc_parent, Path) or not proc_parent.is_absolute():
        _deny("PROC_PARENT_UNTRUSTED")
    values = _show_fields(systemd_show)
    if (
        values["Id"] != _UNIT
        or values["ActiveState"] != "active"
        or values["SubState"] != "running"
        or values["NeedDaemonReload"] != "no"
        or not values["NRestarts"].isdigit()
    ):
        _deny("SYSTEMD_UNIT_NOT_READY")
    pid_string = values["MainPID"]
    if not pid_string.isdigit() or int(pid_string) <= 0:
        _deny("SYSTEMD_MAIN_PID_INVALID")
    pid = int(pid_string)
    release_root = _PARENT + expected_commit
    if (
        _LEGACY in values["WorkingDirectory"]
        or values["WorkingDirectory"] == _LEGACY
    ):
        _deny("LEGACY_SHARED_RUNTIME")
    if values["WorkingDirectory"] != release_root:
        _deny("SYSTEMD_WORKDIR_NOT_PINNED")
    process = proc_parent / str(pid)
    if not process.is_dir() or process.is_symlink():
        _deny("PROC_PROCESS_MISSING_OR_SYMLINK")
    try:
        start_ticks = _stat_start_ticks((process / "stat").read_bytes(), pid)
        command_raw = (process / "cmdline").read_bytes()
        if len(command_raw) > 32 * 1024:
            _deny("PROC_CMDLINE_OVERSIZE")
        command = [
            bit.decode("utf-8") for bit in command_raw.split(b"\0") if bit
        ]
        if len(command) != 2:
            _deny("PROC_CMDLINE_NOT_EXACT")
        argv_python, argv_script = command
        cwd = os.readlink(process / "cwd")
        real_python = os.readlink(process / "exe")
        env = _extract_env((process / "environ").read_bytes())
        after_ticks = _stat_start_ticks((process / "stat").read_bytes(), pid)
    except (OSError, UnicodeError) as exc:
        raise ValueError("EXIT_PROCESS_READ_FAILED") from exc

    if _LEGACY in argv_python or _LEGACY in argv_script or _LEGACY in cwd:
        _deny("LEGACY_SHARED_RUNTIME")
    if start_ticks != after_ticks:
        _deny("PROC_PID_REUSED_OR_CHANGED")
    if (
        argv_python != release_root + "/.venv/bin/python"
        or argv_script != release_root + "/" + _ENTRY
        or cwd != release_root
        or not _PYTHON.fullmatch(real_python)
        or env["CRIPTA_RELEASE_COMMIT"] != expected_commit
        or env["CRIPTA_RUNTIME_ROOT"] != release_root
        or env["PYTHONPATH"] != release_root + "/src:"
        + release_root + "/operations/connectivity"
    ):
        _deny("PROCESS_INSTALLED_RELEASE_NOT_MATCHED")
    return {
        "state": "READ_ONLY_PROCESS_CONSISTENCY_ONLY",
        "component_id": "EXIT",
        "process_unit": _UNIT,
        "pid": pid,
        "pid_start_ticks": start_ticks,
        "n_restarts": int(values["NRestarts"]),
        "process_executable": argv_python,
        "process_entrypoint": argv_script,
        "process_working_directory": cwd,
        "process_kernel_interpreter": real_python,
        "process_pythonpath": env["PYTHONPATH"],
        "loaded_release_commit": env["CRIPTA_RELEASE_COMMIT"],
        "loaded_runtime_root": env["CRIPTA_RUNTIME_ROOT"],
        "caller_supplied_systemd_evidence": True,
        "actual_unit_attestation": False,
        "install_authorized": False,
        "runtime_behavior_verified": False,
        "signed_exchange_protection_verified": False,
    }
