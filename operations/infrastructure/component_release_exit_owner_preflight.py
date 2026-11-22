"""#849 fixed root-owned Exit-only PRE-CUTOVER observer (read-only).

DEV §50.6 / ARCH §50.1. This is the first safe slice of the future
Exit-scoped management owner: physical shared-lock, OLD rollback artifact,
unchanged original Exit unit, stable active Exit and neighbor process
snapshots. It CANNOT install, stop, start, restart or switch any unit.
No DB, credentials, key paths, signed-proof relay or financial actions.

A pass means only the original Exit baseline remains stable and no NEW
immutable Exit candidate has been installed. Never promote that to a
rollback-ready / cutover-authorized flag.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from component_release_exit_legacy_prepare import OLD_EXIT_COMMIT
from component_release_exit_rollback_attestation import (
    attest_old_exit_candidate_on_host,
)
from component_release_exit_root_bootstrap import (
    _SHARED_LOCK,
    _hold_shared_release_lock,
    inspect_exit_root_tree,
)

_OLD_COMMIT = OLD_EXIT_COMMIT
_EXIT_UNIT = "cripta-universal-exit-consumer.service"
_ENTRY_UNIT = "cripta-universal-entry-consumer.service"
_MAIN_PRIVATE = "cripta-private-runtime.service"
_NATIVE = "cripta-native-public-trade-source.service"
_SUB_UNIT = re.compile(r"cripta-private-runtime@ta-[0-9a-f]{32}\.service\Z")
_UNITS_BASE = (_ENTRY_UNIT, _EXIT_UNIT, _MAIN_PRIVATE, _NATIVE)
_EXIT_UNIT_PATH = Path("/etc/systemd/system") / _EXIT_UNIT
_RELEASES = Path("/srv/cripta/components/exit/releases")
_ORIGINAL_UNIT_SHA256 = (
    "c4a21004e637df794670d7d096c6ffa443c23a881c662b65bebfc11459433248"
)
_SYSTEMD_SHOW_KEYS = frozenset({
    "Id", "ActiveState", "SubState", "MainPID", "NRestarts",
    "NeedDaemonReload", "FragmentPath",
})
_HOST_STATUS_TIMEOUT = 8


def _deny(reason: str) -> None:
    raise ValueError("EXIT_OWNER_" + reason)


def _read_unit_state(output: str, unit: str) -> tuple[int, int]:
    """Strictly parse the seven selected read-only systemd-show fields."""
    if not isinstance(output, str) or len(output) > 8192:
        _deny("SYSTEMD_STATUS_UNTRUSTED")
    attrs: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            _deny("SYSTEMD_STATUS_UNTRUSTED")
        key, value = line.split("=", 1)
        if key in attrs or key not in _SYSTEMD_SHOW_KEYS:
            _deny("SYSTEMD_STATUS_UNTRUSTED")
        attrs[key] = value
    if set(attrs) != _SYSTEMD_SHOW_KEYS:
        _deny("SYSTEMD_STATUS_UNTRUSTED")
    fragment = attrs["FragmentPath"]
    if (
        attrs["Id"] != unit
        or attrs["ActiveState"] != "active"
        or attrs["SubState"] != "running"
        or attrs["NeedDaemonReload"] != "no"
        or not attrs["MainPID"].isascii()
        or not attrs["MainPID"].isdigit()
        or not attrs["NRestarts"].isascii()
        or not attrs["NRestarts"].isdigit()
        or int(attrs["MainPID"]) <= 0
        or not fragment.startswith("/etc/systemd/system/")
    ):
        _deny("SYSTEMD_UNIT_NOT_STABLE")
    if unit == _EXIT_UNIT and fragment != "/etc/systemd/system/" + _EXIT_UNIT:
        _deny("EXIT_UNIT_NOT_ORIGINAL_SYSTEMD_PATH")
    return int(attrs["MainPID"]), int(attrs["NRestarts"])


def _show_unit(unit: str) -> tuple[int, int]:
    if unit not in _UNITS_BASE and _SUB_UNIT.fullmatch(unit) is None:
        _deny("UNIT_OUTSIDE_EXIT_PRESERVATION_SCOPE")
    cmd = [
        "/usr/bin/systemctl", "show", unit, "--no-pager",
        *("--property=" + key for key in sorted(_SYSTEMD_SHOW_KEYS)),
    ]
    try:
        r = subprocess.run(
            cmd, check=False, capture_output=True, text=True,
            timeout=_HOST_STATUS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("EXIT_OWNER_SYSTEMD_READ_UNAVAILABLE") from exc
    if r.returncode != 0 or r.stderr or not r.stdout:
        _deny("SYSTEMD_READ_FAILED")
    return _read_unit_state(r.stdout, unit)


def _sub_private_units() -> tuple[str, ...]:
    """Discover the exact single live account-scoped SUB private worker."""
    try:
        r = subprocess.run(
            [
                "/usr/bin/systemctl", "list-units", "--all", "--type=service",
                "--plain", "--no-legend", "--no-pager",
            ],
            check=False, capture_output=True, text=True,
            timeout=_HOST_STATUS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("EXIT_OWNER_SUB_DISCOVERY_UNAVAILABLE") from exc
    if r.returncode != 0 or r.stderr or len(r.stdout) > 256 * 1024:
        _deny("SUB_DISCOVERY_NOT_VERIFIED")
    matched: list[str] = []
    for line in r.stdout.splitlines():
        bits = line.split()
        if bits and bits[0] == "●":
            bits = bits[1:]
        if bits and _SUB_UNIT.fullmatch(bits[0]):
            matched.append(bits[0])
    if len(matched) != 1 or len(set(matched)) != 1:
        _deny("EXACT_ONE_SUB_PRIVATE_REQUIRED")
    return tuple(matched)


def _snapshot_units(sub_units: tuple[str, ...]) -> dict[str, tuple[int, int]]:
    if (
        not isinstance(sub_units, tuple)
        or len(sub_units) != 1
        or _SUB_UNIT.fullmatch(sub_units[0]) is None
    ):
        _deny("SUB_PRIVATE_SCOPE_INVALID")
    return {
        "ENTRY": _show_unit(_ENTRY_UNIT),
        "EXIT": _show_unit(_EXIT_UNIT),
        "MAIN_PRIVATE": _show_unit(_MAIN_PRIVATE),
        "NATIVE": _show_unit(_NATIVE),
        "SUB_PRIVATE": _show_unit(sub_units[0]),
    }


def _installed_unit_sha256() -> str:
    """Read exact root-owned, link-free still-installed legacy Exit unit."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(_EXIT_UNIT_PATH, flags)
    except OSError as exc:
        raise ValueError("EXIT_OWNER_ORIGINAL_UNIT_UNREADABLE") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_nlink != 1
            or not 0 < info.st_size <= 16 * 1024
        ):
            _deny("ORIGINAL_UNIT_METADATA_NOT_TRUSTED")
        with os.fdopen(os.dup(fd), "rb") as reader:
            data = reader.read(16 * 1024 + 1)
        if len(data) != info.st_size:
            _deny("ORIGINAL_UNIT_CHANGED_DURING_READ")
        return hashlib.sha256(data).hexdigest()
    finally:
        os.close(fd)


def _candidate_entries() -> tuple[str, ...]:
    """Read names only. Any entry, including symlinks/partial, blocks."""
    if _RELEASES.is_symlink() or not _RELEASES.is_dir():
        _deny("NEW_RELEASE_PARENT_NOT_TRUSTED")
    try:
        return tuple(sorted(x.name for x in _RELEASES.iterdir()))
    except OSError as exc:
        raise ValueError("EXIT_OWNER_RELEASE_DIRECTORY_NOT_READABLE") from exc


def collect_exit_only_owner_preflight_once() -> dict[str, object]:
    """Single NO-ARGUMENT physical management observer, never a deployer."""
    if sys.platform != "linux":
        _deny("LINUX_ONLY")
    if os.geteuid() != 0:
        _deny("ROOT_REQUIRED")
    with _hold_shared_release_lock(path=_SHARED_LOCK, uid=0, gid=0):
        root_state = inspect_exit_root_tree()
        if root_state["state"] != "ROOT_TREE_PREPARED_METADATA_ONLY":
            _deny("ROOT_TREE_NOT_PREPARED")
        previous = attest_old_exit_candidate_on_host()
        if (
            previous.get("state") != "OLD_EXIT_RECOVERY_STAGED_READBACK_PASS"
            or previous.get("source_commit") != _OLD_COMMIT
            or previous.get("actual_host_attested") is not True
            or previous.get("pinned_unit_systemd_syntax_verified") is not True
            or previous.get("rollback_ready") is not False
            or previous.get("install_authorized") is not False
            or previous.get("signed_main_sub_verified") is not False
        ):
            _deny("PROTECTED_PREVIOUS_EXIT_INVALID")
        old_hash = _installed_unit_sha256()
        if old_hash != _ORIGINAL_UNIT_SHA256:
            _deny("INSTALLED_LEGACY_EXIT_UNIT_CHANGED")
        entries = _candidate_entries()
        if entries:
            _deny("CANDIDATE_NOT_REVIEWED")
        subs = _sub_private_units()
        if len(subs) != 1 or _SUB_UNIT.fullmatch(subs[0]) is None:
            _deny("EXACT_ONE_SUB_PRIVATE_REQUIRED")
        before = _snapshot_units(subs)
        after = _snapshot_units(subs)
        if before != after or before.get("EXIT", (0,))[0] <= 0:
            _deny("NEIGHBOR_CHANGED_OR_EXIT_RESTARTED")
        if _installed_unit_sha256() != old_hash:
            _deny("INSTALLED_LEGACY_EXIT_UNIT_CHANGED")
        return {
            "state": "EXIT_PRECUTOVER_NO_NEW_COMPONENT_STAGED",
            "component_id": "EXIT",
            "affected_unit": _EXIT_UNIT,
            "old_commit": _OLD_COMMIT,
            "old_unit_sha256": old_hash,
            "old_exit_pid": before["EXIT"][0],
            "untouched_unit_count": len(before) - 1,
            "root_tree_host_metadata": "PASS",
            "old_rollback_artifact_readback": "PASS_STAGED_ONLY",
            "shared_release_lock_checked": True,
            "neighbor_pid_restart_snapshot_stable": True,
            "new_component_installed": False,
            "new_component_loaded": False,
            "install_authorized": False,
            "cutover_authorized": False,
            "rollback_ready": False,
            "signed_main_sub_verified": False,
            "runtime_behavior_verified": False,
            "financial_mutation_authorized": False,
            "systemd_mutation_performed": False,
        }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 1:
        raise SystemExit("EXIT_OWNER_ARGUMENTS_NOT_ALLOWED")
    try:
        print(json.dumps(collect_exit_only_owner_preflight_once(), sort_keys=True))
    except (OSError, ValueError) as exc:
        # Do not print raw subprocess stderr, account IDs, secrets or config.
        print("EXIT_OWNER_PREFLIGHT_BLOCKED:" + type(exc).__name__)
        raise SystemExit(1) from None
