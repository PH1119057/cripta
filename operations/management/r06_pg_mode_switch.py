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
    """Execute a fixed systemd-owned cripta-actor probe, NEVER switch UID.

    Manager is root for exactly scoped OS operations. Its systemd sandbox has
    NoNewPrivileges=yes: neither runuser nor Python preexec setuid is a valid
    runtime actor handoff. PID 1 creates a separate static User=cripta unit.
    A successful start alone is not proof: correlate current invocation,
    systemd's Unix actor/sandbox metadata, and its journal JSON result.
    """
    if phase not in ("before", "detached", "after", "rollback_local"):
        raise R06ModeSwitchBlocked("R06_UNKNOWN_PROBE_PHASE")
    unit = f"cripta-r06-entry-preflight@{phase}.service"
    template_path = "/etc/systemd/system/cripta-r06-entry-preflight@.service"
    # Type=oneshot + RemainAfterExit=no synchronously executes the fixed
    # User=cripta unit on EACH start. systemd discards InvocationID and the
    # monotonic start timestamp after it becomes inactive, so those cannot
    # be used as post-exit evidence. The successful blocking start result,
    # fixed ExecStart and actor metadata are authoritative instead.
    launch = _call(
        "/usr/bin/systemctl", "start", unit, timeout=75, checked=False,
    )
    metadata = _call(
        "/usr/bin/systemctl", "show",
        "--property=Type,RemainAfterExit,User,Group,NoNewPrivileges,"
        "FragmentPath,ExecMainStatus,Result,ActiveState,ExecStart",
        unit,
    )
    props = dict(
        line.split("=", 1) for line in metadata.stdout.splitlines()
        if "=" in line
    )
    command = props.get("ExecStart", "")
    expected_argv = "argv[]=" + VENV + " " + str(PROBE) + " " + phase + " ;"
    if (
        props.get("Type") != "oneshot"
        or props.get("RemainAfterExit") != "no"
        or props.get("User") != "cripta"
        or props.get("Group") != "cripta"
        or props.get("NoNewPrivileges") != "yes"
        or props.get("FragmentPath") != template_path
        or "path=" + VENV + " ;" not in command
        or expected_argv not in command
    ):
        raise R06ModeSwitchBlocked(
            "R06_PROBE_SYSTEMD_CRIPTA_ACTOR_OR_UNIT_UNVERIFIED"
        )
    if (
        launch.returncode != 0
        or props.get("Result") != "success"
        or props.get("ActiveState") != "inactive"
        or props.get("ExecMainStatus") != "0"
    ):
        raise R06ModeSwitchBlocked(
            "R06_PROBE_SYNCHRONOUS_ONESHOT_NOT_SUCCESS:"
            + str(launch.returncode)
        )

    # The exact immutable code in the separate User=cripta one-shot unit
    # returns 0 ONLY after checking the signed proof and all relevant gates.
    # Its ExitStatus plus current InvocationID are sufficient. Management
    # does not re-open PostgreSQL as root or read protected journal records.
    release = RELEASE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", release):
        raise R06ModeSwitchBlocked("R06_PROBE_CURRENT_RELEASE_UNVERIFIED")
    expected_result = (
        "R06_PHYSICAL_PG_ONLY_NO_ORDER_PASS" if phase == "after"
        else ("R06_ROLLBACK_LOCAL_OFF_PASS" if phase == "rollback_local"
              else "R06_PG_SWITCH_PREFLIGHT_PASS")
    )
    report: dict[str, object] = {
        "result": expected_result,
        "release": release,
        "trading_effect": "NONE",
        "mode": phase,
        "synchronous_cripta_oneshot": True,
    }
    if phase == "after":
        # cripta verified the causal database boundary before returning 0.
        # Root only reads the existing Observer status; never contacts DB
        # or reconstructs private Exchange/account evidence.
        raw = json.loads(
            Path("/var/lib/cripta/universal_entry_observer/status.json")
            .read_text(encoding="utf-8")
        )
        if not isinstance(raw, dict):
            raise R06ModeSwitchBlocked("R06_AFTER_STATUS_INVALID")
        sensor = raw.get("sensor_status")
        source = raw.get("observer_epoch_id")
        if (
            raw.get("state") != "R06_PG_NO_ORDER_SOURCE_ONLY"
            or raw.get("source_commit") != release
            or not isinstance(source, str)
            or re.fullmatch(r"[0-9a-f]{32}", source) is None
            or not isinstance(sensor, dict)
            or sensor.get("observer_market_ws")
               != "DETACHED_BY_R06_MODE"
            or sensor.get("financial_ready") is not False
        ):
            raise R06ModeSwitchBlocked("R06_AFTER_STATUS_PROVENANCE_DRIFT")
        report["source_session"] = source
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


def _verified_rolled_back_history(
    previous: dict[str, object] | None, original: bytes,
) -> list[dict[str, object]]:
    """Bounded same-mode reattempt ONLY after exact verified prior rollback.

    Preserve an append-only attempt audit in the private root marker and the
    previous original 0600 backup. Never clear a partial/ACTIVE/unknown switch,
    reset a cursor, or recover from a mismatched config/release by guessing.
    """
    if previous is None:
        return []
    if previous.get("state") != "ROLLED_BACK":
        raise R06ModeSwitchBlocked("R06_PREVIOUS_SWITCH_RECORD_EXISTS")
    release = previous.get("release")
    original_hash = previous.get("original_sha")
    edited_hash = previous.get("edited_sha")
    if (
        not isinstance(release, str)
        or re.fullmatch(r"[0-9a-f]{40}", release) is None
        or not isinstance(original_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", original_hash) is None
        or not isinstance(edited_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", edited_hash) is None
        or original_hash == edited_hash
        or _sha(original) != original_hash
        or edited_hash != _sha(_new_config(original))
        or not isinstance(previous.get("ended_at"), str)
        or type(previous.get("old_pid")) is not int
        or previous["old_pid"] <= 1
    ):
        raise R06ModeSwitchBlocked("R06_ROLLED_BACK_HISTORY_INVALID")
    if not BACKUP.exists() or BACKUP.is_symlink():
        raise R06ModeSwitchBlocked("R06_RETRY_ORIGINAL_BACKUP_MISSING")
    meta = BACKUP.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(meta.st_mode)
        or meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o600
        or _sha(BACKUP.read_bytes()) != original_hash
    ):
        raise R06ModeSwitchBlocked("R06_RETRY_ORIGINAL_BACKUP_DRIFT")
    history = previous.get("rolled_back_attempts", [])
    if (
        not isinstance(history, list) or len(history) >= 8
        or any(
            not isinstance(item, dict)
            or item.get("original_sha") != original_hash
            or not isinstance(item.get("release"), str)
            for item in history
        )
    ):
        raise R06ModeSwitchBlocked("R06_PRIOR_ATTEMPT_AUDIT_UNVERIFIED")
    return [
        *history,
        {
            "state": "ROLLED_BACK",
            "release": release,
            "ended_at": previous["ended_at"],
            "old_pid": previous["old_pid"],
            "original_sha": original_hash,
            "edited_sha": edited_hash,
        },
    ]


def _pg_only_worker_block_reason() -> str:
    """Expose one safe no-order R06 failure code before legacy auto-recovery.

    The old Observer restarts and overwrites its shared status.json. Recording
    only a sanitized pre-rollback code in the protected unit failure lets the
    operator repair the EXACT failed stage without journal privileges or a
    generic root command. Never output signed account/DB/Exchange payloads.
    """
    try:
        raw = json.loads(
            Path("/var/lib/cripta/universal_entry_observer/status.json")
            .read_text(encoding="utf-8")
        )
        if (
            not isinstance(raw, dict)
            or raw.get("state") != "BLOCKED"
            or raw.get("source_commit")
            != RELEASE.read_text(encoding="utf-8").strip()
        ):
            return "R06_WORKER_FAILURE_NOT_ATTESTED"
        reason = str(raw.get("reason", ""))
        match = re.fullmatch(
            r"R06_PG_ONLY_BLOCKED:[A-Za-z][A-Za-z0-9_]{0,70}:"
            r"((?:R06|P90)_[A-Z0-9_]{1,100})",
            reason,
        )
        return (
            match.group(1) if match is not None
            else "R06_WORKER_FAILURE_NOT_ATTESTED"
        )
    except (OSError, ValueError, TypeError):
        return "R06_WORKER_FAILURE_NOT_ATTESTED"


def _cutover() -> None:
    sha = _verify_release()
    previous = _load_marker()
    if previous is not None and previous.get("state") != "ROLLED_BACK":
        raise R06ModeSwitchBlocked("R06_PREVIOUS_SWITCH_RECORD_EXISTS")
    initial = _require_units()
    _probe("before")
    contents, meta = _mode_source()
    history = _verified_rolled_back_history(previous, contents)
    modified = _new_config(contents)
    if _sha(modified) == _sha(contents):
        raise R06ModeSwitchBlocked("R06_FLAG_IDEMPOTENCY_INVALID")
    record: dict[str, object] = {
        "state": "PREPARED", "release": sha,
        "original_sha": _sha(contents), "edited_sha": _sha(modified),
        "old_pid": initial, "started_at": datetime.now(UTC).isoformat(),
        "rolled_back_attempts": history,
    }
    if previous is None:
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
                raise R06ModeSwitchBlocked(
                    "R06_NEW_PG_ENTRY_UNIT_FAILED_OR_NOT_NEW:"
                    + _pg_only_worker_block_reason()
                )
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
