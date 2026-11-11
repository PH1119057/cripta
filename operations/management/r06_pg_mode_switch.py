#!/usr/bin/python3
"""Exact no-argument-on-the-wire R06 SOURCE_ONLY unit switch and rollback.

Installed root:root from protected GitHub release into /usr/local/libexec.
Only two fixed systemd oneshot units may execute cutover or rollback. NO
financial gate/Strategy/Order mutation. Never run from mutable checkout.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

CONFIG = Path("/etc/cripta/universal_entry_observer.env")
STATE_DIR = Path("/var/lib/cripta/release-controller/r06-pg-switch")
MARKER = STATE_DIR / "state.json"
BACKUP = STATE_DIR / "observer-env.before"
LOCK = Path("/run/lock/cripta-r06-pg-switch.lock")
OBSERVER = "cripta-universal-entry-observer.service"
EXIT = "cripta-universal-exit-consumer.service"
SOURCE = "cripta-native-public-trade-source.service"
CONTROLLER = "cripta-release-controller.service"
RELEASE = Path("/srv/cripta/runtime/current/INSTALLED_COMMIT")
STATE_RELEASE = Path("/var/lib/cripta/release/INSTALLED_COMMIT")
LIVE_SCRIPT = Path("/usr/local/libexec/cripta-r06-switch/r06_pg_mode_switch.py")
RUNTIME_SCRIPT = Path(
    "/srv/cripta/runtime/current/operations/management/r06_pg_mode_switch.py"
)
PROBE = Path(
    "/srv/cripta/runtime/current/operations/management/r06_pg_switch_probe.py"
)
VENV = "/srv/cripta/runtime/current/.venv/bin/python"
TIMEOUT = 660
FLAG = re.compile(r"^\s*CRIPTA_R06_PG_ONLY\s*=", re.MULTILINE)


class R06ModeSwitchBlocked(RuntimeError):
    """No operation without a complete exact release, source and safety proof."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _call(*args: str, timeout: int = 40, checked: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(args), capture_output=True, text=True, timeout=timeout, check=False,
    )
    if checked and result.returncode != 0:
        raise R06ModeSwitchBlocked(
            "R06_FIXED_COMMAND_FAILED:" + str(args[1] if len(args) > 1 else args[0])
        )
    return result


def _is_active(unit: str) -> bool:
    if unit not in (OBSERVER, EXIT, SOURCE, CONTROLLER):
        raise R06ModeSwitchBlocked("R06_SERVICE_SCOPE_NOT_ALLOWED")
    return _call("/usr/bin/systemctl", "is-active", "--quiet", unit, checked=False).returncode == 0


def _pid(unit: str) -> int:
    row = _call("/usr/bin/systemctl", "show", "--property=MainPID",
                "--value", unit)
    try:
        result = int(row.stdout.strip())
    except ValueError as exc:
        raise R06ModeSwitchBlocked("R06_OBSERVER_PID_UNKNOWN") from exc
    if result < 0:
        raise R06ModeSwitchBlocked("R06_OBSERVER_PID_INVALID")
    return result


def _service(action: str) -> None:
    if action not in ("start", "stop"):
        raise R06ModeSwitchBlocked("R06_SERVICE_ACTION_INVALID")
    _call("/usr/bin/systemctl", action, OBSERVER, timeout=50)


def _mode_source() -> tuple[bytes, os.stat_result]:
    if CONFIG.is_symlink():
        raise R06ModeSwitchBlocked("R06_ENV_SYMLINK_FORBIDDEN")
    st = CONFIG.stat(follow_symlinks=False)
    import grp
    expected_group = grp.getgrnam("cripta").gr_gid
    parent = CONFIG.parent.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(st.st_mode) or st.st_uid != 0
        or st.st_gid != expected_group or stat.S_IMODE(st.st_mode) != 0o640
        or CONFIG.parent.is_symlink() or parent.st_uid != 0
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_gid != expected_group
        or stat.S_IMODE(parent.st_mode) != 0o750
    ):
        raise R06ModeSwitchBlocked("R06_ENV_ACTOR_OWNER_MODE_OR_PARENT_DRIFT")
    contents = CONFIG.read_bytes()
    if not contents or len(contents) > 65536 or b"\x00" in contents:
        raise R06ModeSwitchBlocked("R06_ENV_CONTENT_LENGTH_OR_BINARY_INVALID")
    return contents, st


def _new_config(original: bytes) -> bytes:
    try:
        raw = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise R06ModeSwitchBlocked("R06_ENV_NOT_UTF8") from exc
    entries = FLAG.findall(raw)
    if entries:
        raise R06ModeSwitchBlocked("R06_PG_SWITCH_EXISTING_FLAG_CONFLICT")
    if not raw.endswith("\n"):
        raw += "\n"
    return (raw + "CRIPTA_R06_PG_ONLY=ENABLED\n").encode("utf-8")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic(data: bytes, path: Path, *, uid: int, gid: int, mode: int) -> None:
    fd, tempname = tempfile.mkstemp(prefix=".r06-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fchown(handle.fileno(), uid, gid)
            os.fsync(handle.fileno())
        os.replace(tempname, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)


def _write_marker(record: dict[str, object]) -> None:
    if not STATE_DIR.is_dir() or STATE_DIR.is_symlink():
        raise R06ModeSwitchBlocked("R06_PRIVATE_STATE_DIR_MISSING")
    st = STATE_DIR.stat(follow_symlinks=False)
    if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o700:
        raise R06ModeSwitchBlocked("R06_PRIVATE_STATE_DIR_UNSAFE")
    _atomic(
        (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"),
        MARKER, uid=0, gid=0, mode=0o600,
    )


def _load_marker() -> dict[str, object] | None:
    if not MARKER.exists():
        return None
    st = MARKER.stat(follow_symlinks=False)
    if MARKER.is_symlink() or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o600:
        raise R06ModeSwitchBlocked("R06_MARKER_TAMPERED")
    obj = json.loads(MARKER.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise R06ModeSwitchBlocked("R06_MARKER_INVALID")
    return obj


def _verify_release() -> str:
    if os.geteuid() != 0:
        raise R06ModeSwitchBlocked("R06_ROOT_MANAGEMENT_ACTOR_REQUIRED")
    if not LIVE_SCRIPT.is_file() or LIVE_SCRIPT.is_symlink():
        raise R06ModeSwitchBlocked("R06_ROOT_INSTALLED_BINARY_MISSING")
    st = LIVE_SCRIPT.stat(follow_symlinks=False)
    if st.st_uid != 0 or st.st_gid != 0 or stat.S_IMODE(st.st_mode) != 0o755:
        raise R06ModeSwitchBlocked("R06_ROOT_BINARY_METADATA_DRIFT")
    if _sha(LIVE_SCRIPT.read_bytes()) != _sha(RUNTIME_SCRIPT.read_bytes()):
        raise R06ModeSwitchBlocked("R06_VERIFIED_RELEASE_BINARY_BYTES_DRIFT")
    a, b = RELEASE.read_text().strip(), STATE_RELEASE.read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", a) or a != b:
        raise R06ModeSwitchBlocked("R06_INSTALLED_RELEASE_NOT_EXACT")
    if RELEASE.parent.resolve() != Path("/srv/cripta/runtime/releases") / a:
        raise R06ModeSwitchBlocked("R06_RUNTIME_RELEASE_SYMLINK_DRIFT")
    if _is_active(CONTROLLER):
        raise R06ModeSwitchBlocked("R06_RELEASE_CONTROLLER_ACTIVE")
    return a


def _require_units() -> int:
    if not _is_active(SOURCE) or not _is_active(EXIT):
        raise R06ModeSwitchBlocked("R06_SHARED_SOURCE_OR_EXIT_NOT_ACTIVE")
    if not _is_active(OBSERVER):
        raise R06ModeSwitchBlocked("R06_OLD_ENTRY_OBSERVER_INACTIVE")
    p = _pid(OBSERVER)
    if p <= 1:
        raise R06ModeSwitchBlocked("R06_OLD_ENTRY_PID_NOT_PROVEN")
    return p


def _probe(phase: str) -> dict[str, object]:
    if phase not in ("before", "detached", "after", "rollback_local"):
        raise R06ModeSwitchBlocked("R06_UNKNOWN_PROBE_PHASE")
    command = (
        "/usr/sbin/runuser", "-u", "cripta", "--",
        "/usr/bin/env", "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONPATH=/srv/cripta/runtime/current/src:/srv/cripta/runtime/current",
        VENV, str(PROBE), phase,
    )
    result = _call(*command, timeout=65, checked=False)
    try:
        report = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise R06ModeSwitchBlocked("R06_SIGNED_SOURCE_PROBE_OUTPUT_INVALID") from exc
    if (
        result.returncode != 0 or not isinstance(report, dict)
        or report.get("trading_effect") != "NONE"
        or report.get("result") not in (
            "R06_PG_SWITCH_PREFLIGHT_PASS",
            "R06_PHYSICAL_PG_ONLY_NO_ORDER_PASS",
            "R06_ROLLBACK_LOCAL_OFF_PASS",
        )
    ):
        raise R06ModeSwitchBlocked(
            "R06_SIGNED_SOURCE_PROBE_BLOCKED:" +
            str(report.get("reason", "UNKNOWN"))[:110]
        )
    return report


def _check_unit_invocation(mode: str) -> None:
    wanted = (
        "cripta-r06-entry-cutover.service" if mode == "cutover"
        else ("cripta-r06-entry-recovery.service" if mode == "auto-rollback"
              else "cripta-r06-entry-rollback.service")
    )
    try:
        cgroup = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError as exc:
        raise R06ModeSwitchBlocked("R06_MANAGEMENT_UNIT_CGROUP_UNAVAILABLE") from exc
    if "/system.slice/" + wanted not in cgroup or not os.environ.get("INVOCATION_ID"):
        raise R06ModeSwitchBlocked("R06_SCOPED_UNIT_INVOCATION_REQUIRED")


def _rollback_inner(*, automatic: bool) -> None:
    data = _load_marker()
    if data is None:
        if automatic:
            return
        raise R06ModeSwitchBlocked("R06_NO_SWITCH_RECORD_FOR_ROLLBACK")
    state = data.get("state")
    if state in ("ROLLED_BACK", "PREPARED_BEFORE_STOP"):
        if state == "PREPARED_BEFORE_STOP" and automatic:
            return
        if state == "ROLLED_BACK":
            return
    if automatic and state == "ACTIVE":
        # A repeated cutover must NEVER undo a previously accepted release.
        return
    if state not in ("PREPARED", "FLAG_WRITTEN", "STARTED", "WAITING", "ACTIVE"):
        raise R06ModeSwitchBlocked("R06_ROLLBACK_STATE_AMBIGUOUS")
    release = _verify_release()
    if data.get("release") != release:
        raise R06ModeSwitchBlocked("R06_ROLLBACK_RELEASE_LINEAGE_CHANGED")
    original, edited = data.get("original_sha"), data.get("edited_sha")
    if not isinstance(original, str) or not isinstance(edited, str):
        raise R06ModeSwitchBlocked("R06_ROLLBACK_HASHES_MISSING")
    if not BACKUP.exists() or BACKUP.is_symlink():
        raise R06ModeSwitchBlocked("R06_ROLLBACK_PRIVATE_BACKUP_MISSING")
    backup_st = BACKUP.stat(follow_symlinks=False)
    if backup_st.st_uid != 0 or stat.S_IMODE(backup_st.st_mode) != 0o600:
        raise R06ModeSwitchBlocked("R06_ROLLBACK_BACKUP_METADATA_DRIFT")
    payload = BACKUP.read_bytes()
    if _sha(payload) != original:
        raise R06ModeSwitchBlocked("R06_ROLLBACK_BACKUP_HASH_DRIFT")
    current, meta = _mode_source()
    if _sha(current) not in (original, edited):
        raise R06ModeSwitchBlocked("R06_ROLLBACK_CONFIG_CONCURRENT_CHANGE")
    # A blocked signed MAIN/SUB forward preflight must not itself prevent
    # restoring the already-existing no-order legacy Observer. Verify local
    # OFF/exposure under cripta, plus both independent source/Exit services.
    # The old Observer still has its own fail-closed private safety gates.
    if not _is_active(SOURCE) or not _is_active(EXIT):
        raise R06ModeSwitchBlocked("R06_ROLLBACK_EXIT_OR_SOURCE_UNAVAILABLE")
    _probe("rollback_local")
    if _sha(current) == original and _is_active(OBSERVER):
        data["state"] = "ROLLED_BACK"
        data["ended_at"] = datetime.now(UTC).isoformat()
        _write_marker(data)
        return
    if _is_active(OBSERVER):
        _service("stop")
    if _is_active(OBSERVER):
        raise R06ModeSwitchBlocked("R06_ROLLBACK_OLD_OR_NEW_ENTRY_STILL_RUNNING")
    if _sha(current) == edited:
        _atomic(payload, CONFIG, uid=meta.st_uid, gid=meta.st_gid, mode=0o640)
    _service("start")
    if not _is_active(OBSERVER) or _pid(OBSERVER) <= 1:
        raise R06ModeSwitchBlocked("R06_ROLLBACK_LEGACY_OBSERVER_NOT_RESTORED")
    data["state"] = "ROLLED_BACK"
    data["ended_at"] = datetime.now(UTC).isoformat()
    _write_marker(data)


def _cutover() -> None:
    sha = _verify_release()
    if _load_marker() is not None:
        raise R06ModeSwitchBlocked("R06_PREVIOUS_SWITCH_RECORD_EXISTS")
    initial = _require_units()
    _probe("before")
    contents, meta = _mode_source()
    modified = _new_config(contents)
    if _sha(modified) == _sha(contents):
        raise R06ModeSwitchBlocked("R06_FLAG_IDEMPOTENCY_INVALID")
    record: dict[str, object] = {
        "state": "PREPARED", "release": sha,
        "original_sha": _sha(contents), "edited_sha": _sha(modified),
        "old_pid": initial, "started_at": datetime.now(UTC).isoformat(),
    }
    _atomic(contents, BACKUP, uid=0, gid=0, mode=0o600)
    _write_marker(record)
    try:
        _service("stop")
        if _is_active(OBSERVER) or _pid(OBSERVER) != 0:
            raise R06ModeSwitchBlocked("R06_OLD_ENTRY_READER_NOT_DETACHED")
        _probe("detached")  # fresh signed FLAT after old public reader stopped
        _atomic(modified, CONFIG, uid=meta.st_uid, gid=meta.st_gid, mode=0o640)
        record["state"] = "FLAG_WRITTEN"
        _write_marker(record)
        _service("start")
        record["state"] = "WAITING"
        _write_marker(record)
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if not _is_active(OBSERVER) or _pid(OBSERVER) in (0, initial):
                raise R06ModeSwitchBlocked("R06_NEW_PG_ENTRY_UNIT_FAILED_OR_NOT_NEW")
            if not _is_active(EXIT) or not _is_active(SOURCE):
                raise R06ModeSwitchBlocked("R06_SHARED_EXIT_OR_MARKET_SOURCE_LOST")
            try:
                report = _probe("after")
            except R06ModeSwitchBlocked:
                # The independently verified completed-5m causal boundary
                # may not yet have arrived; preserve no-order waiting.
                time.sleep(2)
                continue
            if report.get("release") != sha:
                raise R06ModeSwitchBlocked("R06_NEW_PG_ENTRY_RELEASE_MISMATCH")
            record["state"] = "ACTIVE"
            record["new_pid"] = _pid(OBSERVER)
            record["source_session"] = report["source_session"]
            record["accepted_at"] = datetime.now(UTC).isoformat()
            _write_marker(record)
            print(json.dumps({
                "result": "R06_PG_ONLY_CUTOVER_NO_ORDER_PASS",
                "release": sha, "new_pid": record["new_pid"],
                "source_session": record["source_session"],
                "trading_effect": "NONE",
            }, sort_keys=True))
            return
        raise R06ModeSwitchBlocked("R06_NEW_PUBLISHED_BOUNDARY_TIMEOUT")
    except Exception:
        # Failure restores old Entry reader only after exact no-orders proof.
        _rollback_inner(automatic=True)
        raise


def main() -> int:
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in (
            "cutover", "rollback", "auto-rollback",
        ):
            raise R06ModeSwitchBlocked("R06_FIXED_SYSTEMD_ACTION_REQUIRED")
        mode = sys.argv[1]
        _check_unit_invocation(mode)
        import fcntl
        lock_fd = os.open(
            LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        with os.fdopen(lock_fd, "r+b") as lock:
            lock_info = os.fstat(lock.fileno())
            if lock_info.st_uid != 0 or not stat.S_ISREG(lock_info.st_mode):
                raise R06ModeSwitchBlocked("R06_LOCK_OWNER_OR_TYPE_DRIFT")
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if mode == "cutover":
                _cutover()
            else:
                _rollback_inner(automatic=mode == "auto-rollback")
                print(json.dumps({
                    "result": "R06_PG_ONLY_ROLLBACK_VERIFIED",
                    "trading_effect": "NONE",
                }, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({
            "result": "R06_PG_SWITCH_BLOCKED",
            "type": type(exc).__name__,
            "reason": str(exc)[:170],
            "trading_effect": "NONE",
        }, sort_keys=True), file=sys.stderr)
        return 42


if __name__ == "__main__":
    raise SystemExit(main())
