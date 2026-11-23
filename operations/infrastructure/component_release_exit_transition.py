"""#849 Exit-only scoped OS transition with reconciliation-first rollback.

This is the MECHANICAL owner/actuator, not a release grant.
No-argument production entrypoint fails closed until independently verified:
merged scoped GitHub source+CI, sealed candidate/root+ABI, true OLD
restart/recovery, original open-position/ExitPlan continuity, fresh
credential-local signed MAIN/SUB inventory, frozen neighboring units,
sandbox/Unix actor and durable journal. No bypass via caller-supplied values.

The internal transition has actual fixed-target systemd/atomic unit IO and
an append/fsync journal. Tests inject an in-memory service actor. Do not
invoke the internal transition on live services or through source sync/CI.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from component_release_exit_owner_preflight import (
    _EXIT_UNIT,
    _EXIT_UNIT_PATH,
    _read_unit_state,
    _snapshot_units,
    _sub_private_units,
)
from component_release_exit_process_readback import (
    collect_exit_process_readback,
)
from component_release_exit_root_bootstrap import (
    _EXIT_JOURNAL,
    _SHARED_LOCK,
    _hold_shared_release_lock,
    inspect_exit_journal_root_once,
    inspect_exit_root_tree,
)
from component_release_exit_signed_preflight import (
    _compare_recovery_snapshots,
)
from component_release_exit_unit_binding import (
    _ARCHIVE_SHA256, _BINDING_NAME, _COMPONENT, _COMPONENT_ROOT,
    _LOCK_SHA256, _ROOT, _SOURCE, _UNIT_SHA256, _readback_binding_fixture,
)
from component_release_exit_new_install import _readback_prepared_candidate
from component_release_exit_rollback_attestation import (
    attest_old_exit_candidate_on_host,
)
from component_release_exit_unit_spec import build_exit_unit_candidate

_UNIT_PARENT = Path("/etc/systemd/system")
_JOURNAL_PARENT = _EXIT_JOURNAL
_JOURNAL_NAME = "exit-only-current.jsonl"
_MAX_UNIT_BYTES = 16 * 1024
_SYSTEMCTL = "/usr/bin/systemctl"
_SYSTEMD_FIELDS = (
    "Id", "ActiveState", "SubState", "MainPID", "NRestarts",
    "NeedDaemonReload", "FragmentPath",
)
_STATES = frozenset({
    "OLD_ACTIVE", "OLD_STOPPED", "NEW_ACTIVE", "NEW_STOPPED",
})
_ACTION_TIMEOUT = 30

_EXIT_STATUS = Path("/var/lib/cripta-p90-r06-exit/status.json")
_SIGNED_READER_TIMEOUT = 35
_SIGNED_SOURCE_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "PYTHONPATH": (
        "/srv/cripta/source_checkout/src:"
        "/srv/cripta/source_checkout/operations/infrastructure:"
        "/srv/cripta/source_checkout/operations/connectivity"
    ),
}
_SIGNED_GET_PROGRAM = (
    "import json; "
    "from component_release_exit_signed_preflight "
    "import collect_current_signed_exit_preflight_once; "
    "print(json.dumps(collect_current_signed_exit_preflight_once(), "
    "sort_keys=True))"
)

_CONTROLLER_ROOT = _ROOT / ".owner-controller-849"
_CONTROLLER_MANIFEST = "SOURCE_MANIFEST.json"
_EXPECTED_MODULES = frozenset({
    "component_release_exit_transition.py",
    "component_release_exit_owner_preflight.py",
    "component_release_exit_legacy_prepare.py",
    "component_release_exit_new_install.py",
    "component_release_exit_rollback_attestation.py",
    "component_release_exit_root_bootstrap.py",
    "component_release_exit_signed_preflight.py",
    "component_release_exit_process_readback.py",
    "component_release_exit_unit_binding.py",
    "component_release_exit_unit_spec.py",
})
_SHA40 = re.compile(r"[0-9a-f]{40}\\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\\Z")
_FLAT_GATE_PROGRAM = (
    "import psycopg; "
    "c=psycopg.connect('dbname=cripta user=cripta "
    "host=/var/run/postgresql connect_timeout=5'); "
    "c.execute('SET TRANSACTION READ ONLY'); "
    "a=c.execute(\"SELECT COUNT(*) FROM control.execution_gates "
    "WHERE mode='mainnet' AND enabled=0\").fetchone()[0]; "
    "b=c.execute(\"SELECT COUNT(*) FROM control.live_arm_sessions "
    "WHERE state<>'CLOSED'\").fetchone()[0]; "
    "d=c.execute(\"SELECT COUNT(*) FROM control.account_live_arm_sessions "
    "WHERE state<>'DISARMED'\").fetchone()[0]; "
    "e=c.execute(\"SELECT COUNT(*) FROM strategy_entry.execution_permissions "
    "WHERE enabled\").fetchone()[0]; "
    "print('FLAT_ENTRY_INHIBITED' if a==1 and b==d==e==0 else 'BLOCKED')"
)
_FLAT_GATE_TIMEOUT = 16
_MAX_JOURNAL_BYTES = 64 * 1024


def _deny(code: str) -> None:
    raise ValueError("EXIT_TRANSITION_" + code)


def _digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _unit_material_ok(old: object, new: object) -> bool:
    return (
        isinstance(old, bytes) and isinstance(new, bytes)
        and 0 < len(old) <= _MAX_UNIT_BYTES
        and 0 < len(new) <= _MAX_UNIT_BYTES
        and old != new
    )


def _result(state: str, *, rollback_verified: bool = False) -> dict[str, object]:
    # This internal engine can never assert a finished live release or an
    # independently signed account proof. Those are external admission gates.
    return {
        "state": state,
        "affected_unit": _EXIT_UNIT,
        "rollback_verified": rollback_verified,
        "release_authorized": False,
        "financial_mutation_authorized": False,
        "live_deployment_verified": False,
        "runtime_behavior_verified": False,
        "signed_exchange_protection_independently_verified": False,
    }


def _recover_exact_old(
    actuator: object,
    journal: object,
    *,
    observed: str,
    old_unit: bytes,
    new_unit: bytes,
    previous_neighbors: object,
) -> bool:
    """One bounded rollback attempt; never retry a command with UNKNOWN result.

    The actor's reconcile() is required to read actual exact on-disk unit
    digest, systemd state, process and PID. An ambiguous state is a STOP.
    """
    if observed not in _STATES:
        return False
    journal.record("ROLLBACK_INTENT")
    if observed == "NEW_ACTIVE":
        journal.record("ROLLBACK_STOP_INTENT")
        actuator.stop()
        actuator.prove_stopped()
        observed = "NEW_STOPPED"
    if observed in ("NEW_STOPPED",):
        journal.record("ROLLBACK_UNIT_WRITE_INTENT")
        actuator.install_unit(new_unit, old_unit)
        journal.record("ROLLBACK_RELOAD_INTENT")
        actuator.reload()
        observed = "OLD_STOPPED"
    if observed == "OLD_STOPPED":
        journal.record("ROLLBACK_START_INTENT")
        actuator.start()
    journal.record("ROLLBACK_VERIFY_INTENT")
    actuator.verify_old()
    actuator.verify_signed_and_recovery()
    actuator.neighbors_unchanged(previous_neighbors)
    journal.record("ROLLBACK_RECOVERED")
    return True


def _run_exit_only_transition(
    actuator: object, journal: object, *,
    old_unit: bytes, new_unit: bytes,
) -> dict[str, object]:
    """Testable mechanical transaction; trusted LIVE caller NOT provided.

    Future no-argument owner MUST own an exclusive release lock, durable
    prepared candidate, verified protection and live old-restart proof BEFORE
    calling this engine. Synthetic actuator/journal does not authorize use.
    """
    if not _unit_material_ok(old_unit, new_unit):
        _deny("UNIT_INPUT_INVALID")

    try:
        prior = actuator.before()
        journal.record("PREPARED")
        journal.record("STOP_INTENT")
    except Exception:
        # Must not issue a systemd call if even PREPARED cannot be durable.
        return _result("EXIT_PRESTOP_BLOCKED")

    uncertain_audit = False
    try:
        actuator.stop()
        journal.record("STOP_DONE")
        actuator.prove_stopped()
        journal.record("UNIT_WRITE_INTENT")
        actuator.install_unit(old_unit, new_unit)
        journal.record("UNIT_WRITTEN")
        journal.record("RELOAD_INTENT")
        actuator.reload()
        journal.record("RELOADED")
        journal.record("START_INTENT")
        actuator.start()
        journal.record("STARTED")
        journal.record("POSTCHECK_INTENT")
        actuator.verify_new()
        actuator.verify_signed_and_recovery()
        actuator.neighbors_unchanged(prior)
        journal.record("ACCEPTANCE_PENDING")
        return _result("EXIT_TRANSITION_ACCEPTANCE_PENDING")
    except Exception as exc:
        # A journal write error after the first action removes the durable
        # proof chain; even a successful physical restore is NOT VERIFIED.
        uncertain_audit = isinstance(exc, OSError)
        try:
            observed = actuator.reconcile()
        except Exception:
            return _result("EXIT_OUTCOME_UNKNOWN_RECONCILE")
        if observed not in _STATES:
            return _result("EXIT_OUTCOME_UNKNOWN_STOP")
        try:
            restored = _recover_exact_old(
                actuator, journal, observed=observed,
                old_unit=old_unit, new_unit=new_unit,
                previous_neighbors=prior,
            )
        except Exception:
            return _result("EXIT_ROLLBACK_NOT_VERIFIED")
        if not restored or uncertain_audit:
            return _result("EXIT_ROLLBACK_NOT_VERIFIED")
        return _result("EXIT_OLD_RECOVERED_AFTER_FAILURE", rollback_verified=True)



def _collect_credential_local_exit_snapshot() -> dict[str, object]:
    """Exactly one cripta-UID signed GET + PostgreSQL READ ONLY, no shell.

    No credentials are shared with the root release owner. Output is a
    short-lived *untrusted* snapshot until independently cross-checked.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        _deny("ROOT_LINUX_REQUIRED")
    try:
        result = subprocess.run(
            [
                "/usr/sbin/runuser", "-u", "cripta", "--",
                "/srv/cripta/runtime/current/.venv/bin/python", "-B",
                "-c", _SIGNED_GET_PROGRAM,
            ],
            env=_SIGNED_SOURCE_ENV,
            cwd="/srv/cripta/source_checkout",
            check=False, capture_output=True, text=True,
            timeout=_SIGNED_READER_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("EXIT_SIGNED_READ_OUTCOME_UNKNOWN") from exc
    if (
        result.returncode != 0 or result.stderr
        or not 0 < len(result.stdout) <= 16 * 1024
    ):
        raise RuntimeError("EXIT_CREDENTIAL_LOCAL_SIGNED_READ_BLOCKED")
    try:
        output = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("EXIT_SIGNED_READ_JSON_UNTRUSTED") from exc
    if (
        not isinstance(output, dict)
        or output.get("state") != "SIGNED_MAIN_SUB_GET_CONSISTENCY_PASS"
        or output.get("position_exit_plan_alignment")
        != "SIGNED_POSITION_EXIT_PLAN_DB_ALIGNMENT_PASS"
        or output.get("release_authorized") is not False
        or output.get("financial_mutation_authorized") is not False
        or output.get("runtime_behavior_verified") is not False
        or not isinstance(output.get("existing_position_recovery_snapshot"), dict)
    ):
        raise RuntimeError("EXIT_SIGNED_READ_PROVENANCE_UNVERIFIED")
    return output["existing_position_recovery_snapshot"]


def _read_protected_exit_status() -> dict[str, object]:
    """Read only the one existing cripta-owned status file with no symlinks."""
    if sys.platform != "linux" or os.geteuid() != 0:
        _deny("ROOT_LINUX_REQUIRED")
    import grp
    import pwd

    cripta_uid = pwd.getpwnam("cripta").pw_uid
    cripta_gid = grp.getgrnam("cripta").gr_gid
    parent = _EXIT_STATUS.parent
    info = parent.lstat()
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        != (cripta_uid, cripta_gid, 0o750)
    ):
        raise RuntimeError("EXIT_STATUS_PARENT_UNTRUSTED")
    fd = os.open(
        _EXIT_STATUS,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        file_info = os.fstat(fd)
        if (
            not stat.S_ISREG(file_info.st_mode)
            or file_info.st_nlink != 1
            or (file_info.st_uid, file_info.st_gid,
                stat.S_IMODE(file_info.st_mode))
            != (cripta_uid, cripta_gid, 0o644)
            or not 0 < file_info.st_size <= 4096
        ):
            raise RuntimeError("EXIT_STATUS_FILE_UNTRUSTED")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(4097)
        if len(raw) != file_info.st_size:
            raise RuntimeError("EXIT_STATUS_CHANGED_DURING_READ")
    finally:
        os.close(fd)
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("EXIT_STATUS_JSON_INVALID") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("EXIT_STATUS_JSON_INVALID")
    return payload


def _validate_flat_exit_status(
    status: object, *, observed_at: datetime,
    not_before: datetime | None,
) -> None:
    """A fresh READY_FLAT heartbeat; never used as open-position E2E proof."""
    if not isinstance(status, dict):
        raise RuntimeError("EXIT_STATUS_UNTRUSTED")
    required = {
        "service", "state", "result", "release_commit",
        "p90_r06_exit_owner", "generic_consumer", "observed_at", "error",
    }
    if not required.issubset(status) or not set(status).issubset(
        required | {"recovery_result"}
    ):
        raise RuntimeError("EXIT_STATUS_SHAPE_UNTRUSTED")
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None or observed_at.utcoffset() is None
    ):
        raise RuntimeError("EXIT_HEARTBEAT_CLOCK_UNTRUSTED")
    if any((
        status.get("service") != "p90_r06_exit_owner",
        status.get("state") != "P90_R06_EXIT_OWNER_READY_FLAT",
        status.get("result") != "P90_R06_EXIT_OWNER_READY_FLAT",
        status.get("release_commit") != _SOURCE,
        status.get("p90_r06_exit_owner") != "ENABLED",
        status.get("generic_consumer") != "DISABLED",
        status.get("error") is not None,
    )):
        raise RuntimeError("EXIT_RUNTIME_READY_FLAT_NOT_PROVEN")
    try:
        heartbeat = datetime.fromisoformat(status["observed_at"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("EXIT_HEARTBEAT_TIMESTAMP_UNTRUSTED") from exc
    if heartbeat.tzinfo is None or heartbeat.utcoffset() is None:
        raise RuntimeError("EXIT_HEARTBEAT_TIMESTAMP_UNTRUSTED")
    delta = (observed_at.astimezone(UTC) - heartbeat.astimezone(UTC)).total_seconds()
    if not 0 <= delta <= 20:
        raise RuntimeError("EXIT_HEARTBEAT_STALE_OR_FUTURE")
    if not_before is not None:
        if (
            not isinstance(not_before, datetime)
            or not_before.tzinfo is None
            or not_before.utcoffset() is None
            or heartbeat.astimezone(UTC) < not_before.astimezone(UTC)
        ):
            raise RuntimeError("EXIT_HEARTBEAT_NOT_FROM_RESTARTED_CONSUMER")


def _check_flat_new_entry_inhibited() -> None:
    """Credential-local PG READ ONLY fresh gate, no financial write rights."""
    if sys.platform != "linux" or os.geteuid() != 0:
        _deny("ROOT_LINUX_REQUIRED")
    try:
        outcome = subprocess.run(
            [
                "/usr/sbin/runuser", "-u", "cripta", "--",
                "/srv/cripta/runtime/current/.venv/bin/python", "-B", "-c",
                _FLAT_GATE_PROGRAM,
            ],
            env=_SIGNED_SOURCE_ENV,
            cwd="/srv/cripta/source_checkout",
            check=False, capture_output=True, text=True,
            timeout=_FLAT_GATE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("EXIT_NEW_ENTRY_INHIBITION_UNKNOWN") from exc
    if (
        outcome.returncode != 0 or outcome.stderr
        or outcome.stdout.strip() != "FLAT_ENTRY_INHIBITED"
    ):
        raise RuntimeError("EXIT_NEW_ENTRY_INHIBITION_NOT_PROVEN")


def _check_sealed_controller_root() -> None:
    """Reject all source_checkout/root PYTHONPATH entrypoint impersonation."""
    if sys.platform != "linux" or os.geteuid() != 0:
        _deny("ROOT_LINUX_REQUIRED")
    import grp

    directory = _CONTROLLER_ROOT
    if Path(__file__).parent != directory or directory.is_symlink():
        _deny("ROOT_CONTROLLER_NOT_SEALED")
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
        or info.st_gid != grp.getgrnam("cripta").gr_gid
        or stat.S_IMODE(info.st_mode) != 0o750
    ):
        _deny("ROOT_CONTROLLER_METADATA_UNTRUSTED")
    found = set(item.name for item in directory.iterdir())
    if found != _EXPECTED_MODULES | {_CONTROLLER_MANIFEST}:
        _deny("ROOT_CONTROLLER_FILE_SET_CHANGED")
    manifest_path = directory / _CONTROLLER_MANIFEST
    marker_info = manifest_path.lstat()
    if (
        not stat.S_ISREG(marker_info.st_mode)
        or marker_info.st_uid != 0 or marker_info.st_gid != 0
        or stat.S_IMODE(marker_info.st_mode) != 0o600
        or marker_info.st_nlink != 1 or marker_info.st_size > 4096
    ):
        _deny("ROOT_CONTROLLER_MANIFEST_UNTRUSTED")
    try:
        record = json.loads(manifest_path.read_text("ascii"))
    except (OSError, ValueError) as exc:
        raise ValueError("EXIT_TRANSITION_ROOT_CONTROLLER_MANIFEST_INVALID") from exc
    if (
        not isinstance(record, dict)
        or set(record) != {"schema", "source_commit", "files",
                           "component", "authorized_scope"}
        or record["schema"] != "EXIT_ONLY_ROOT_OWNER_V1"
        or not isinstance(record["source_commit"], str)
        or _SHA40.fullmatch(record["source_commit"]) is None
        or record["component"] != "EXIT"
        or record["authorized_scope"] != "UNIT_CUTOVER_NO_ARM_NO_DB_WRITES"
        or not isinstance(record["files"], dict)
        or set(record["files"]) != _EXPECTED_MODULES
    ):
        _deny("ROOT_CONTROLLER_SOURCE_MANIFEST_DRIFT")
    for name in sorted(_EXPECTED_MODULES):
        file = directory / name
        file_info = file.lstat()
        if (
            not stat.S_ISREG(file_info.st_mode)
            or file_info.st_uid != 0 or file_info.st_gid != 0
            or stat.S_IMODE(file_info.st_mode) != 0o640
            or file_info.st_nlink != 1 or file_info.st_size > 64 * 1024
            or not isinstance(record["files"][name], str)
            or _SHA256.fullmatch(record["files"][name]) is None
            or hashlib.sha256(file.read_bytes()).hexdigest()
            != record["files"][name]
        ):
            _deny("ROOT_CONTROLLER_SOURCE_BYTES_DRIFT")


def _load_exact_exit_units() -> tuple[bytes, bytes]:
    """Read immutable OLD and NEW artifacts; never select user-supplied paths."""
    import grp

    if inspect_exit_root_tree().get("state") != "ROOT_TREE_PREPARED_METADATA_ONLY":
        _deny("PROTECTED_EXIT_ROOT_UNTRUSTED")
    if (
        inspect_exit_journal_root_once().get("state")
        != "PROTECTED_EXIT_JOURNAL_ROOT_READY_READ_ONLY"
    ):
        _deny("PROTECTED_EXIT_JOURNAL_UNTRUSTED")
    old = attest_old_exit_candidate_on_host()
    if (
        old.get("state") != "OLD_EXIT_RECOVERY_STAGED_READBACK_PASS"
        or old.get("actual_host_attested") is not True
        or old.get("rollback_ready") is not False
    ):
        _deny("EXACT_OLD_ROLLBACK_ARTIFACT_DRIFT")
    new = _readback_prepared_candidate(
        candidate_root=_COMPONENT_ROOT,
        owner_uid=0,
        service_gid=grp.getgrnam("cripta").gr_gid,
        expected_commit=_COMPONENT,
        expected_archive_sha256=_ARCHIVE_SHA256,
    )
    if (
        new.get("state") != "IMMUTABLE_EXIT_CANDIDATE_PREPARED"
        or new.get("physical_file_hashes_verified") is not True
        or new.get("cutover_authorized") is not False
        or new.get("new_component_loaded") is not False
    ):
        _deny("EXACT_NEW_COMPONENT_DRIFT")
    from component_release_exit_legacy_prepare import _INPUT

    old_bytes = (_INPUT / "legacy_unit.service").read_bytes()
    if _digest(old_bytes) != (
        "c4a21004e637df794670d7d096c6ffa443c23a881c662b65bebfc11459433248"
    ):
        _deny("EXACT_OLD_UNIT_BYTES_DRIFT")
    new_bytes = build_exit_unit_candidate(
        source_unit=old_bytes.decode("utf-8"),
        component_commit=_COMPONENT,
        component_lock_sha256=_LOCK_SHA256,
        source_compatibility_commit=_SOURCE,
    )["unit_text"].encode("utf-8")
    if _digest(new_bytes) != _UNIT_SHA256:
        _deny("EXACT_NEW_UNIT_BYTES_DRIFT")
    binding = _readback_binding_fixture(
        parent=_ROOT, owner_uid=0,
        service_gid=grp.getgrnam("cripta").gr_gid,
        binding_name=_BINDING_NAME, expected_unit=new_bytes,
    )
    if (
        binding.get("state") != "PROTECTED_EXIT_UNIT_BINDING_PREPARED_ONLY"
        or binding.get("file_digest_readback") is not True
    ):
        _deny("PROTECTED_NEW_UNIT_BINDING_DRIFT")
    marker = Path("/var/lib/cripta/release/INSTALLED_COMMIT")
    if marker.is_symlink() or marker.read_text("ascii").strip() != _SOURCE:
        _deny("LEGACY_MAIN_RELEASE_CHANGED")
    return old_bytes, new_bytes



class _HostExitUnitIO:
    """OS implementation with exactly one hardcoded, protected Exit unit.

    Requires root, independently authenticated no-arg owner, verified release
    lock, reviewed candidate unit, recovery proof and test environment before
    invoking. This class is NOT a public installer.
    """

    def __init__(self, *, old_unit: bytes, new_unit: bytes) -> None:
        if sys.platform != "linux" or os.geteuid() != 0:
            _deny("ROOT_LINUX_REQUIRED")
        if not _unit_material_ok(old_unit, new_unit):
            _deny("UNIT_INPUT_INVALID")
        self._old = old_unit
        self._new = new_unit
        self._sub_units = _sub_private_units()
        self._pre_signed_snapshot: dict[str, object] | None = None
        self._restart_started_at: datetime | None = None

    def _cmd(self, *args: str) -> str:
        try:
            result = subprocess.run(
                [_SYSTEMCTL, *args], check=False, capture_output=True,
                text=True, timeout=_ACTION_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("EXIT_SYSTEMD_OUTCOME_UNVERIFIED") from exc
        if result.returncode != 0 or result.stderr:
            # No subprocess stderr/raw host detail in outward exception.
            raise RuntimeError("EXIT_SYSTEMD_ACTION_FAILED")
        return result.stdout

    def _show(self, *, allow_reload_pending: bool = False) -> dict[str, str]:
        raw = self._cmd("show", _EXIT_UNIT, "--no-pager", *(
            "--property=" + key for key in _SYSTEMD_FIELDS
        ))
        fields: dict[str, str] = {}
        for line in raw.splitlines():
            key, sep, value = line.partition("=")
            if not sep or key in fields or key not in _SYSTEMD_FIELDS:
                raise RuntimeError("EXIT_SYSTEMD_READBACK_MALFORMED")
            fields[key] = value
        if set(fields) != set(_SYSTEMD_FIELDS):
            raise RuntimeError("EXIT_SYSTEMD_READBACK_INCOMPLETE")
        if (
            fields["Id"] != _EXIT_UNIT
            or fields["FragmentPath"] != str(_EXIT_UNIT_PATH)
            or fields["NeedDaemonReload"] not in (
                ("no", "yes") if allow_reload_pending else ("no",)
            )
        ):
            raise RuntimeError("EXIT_SYSTEMD_UNIT_IDENTITY_DRIFT")
        return fields

    def _unit_bytes(self) -> bytes:
        info = _EXIT_UNIT_PATH.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_nlink != 1 or not (0 < info.st_size <= _MAX_UNIT_BYTES)
        ):
            raise RuntimeError("EXIT_UNIT_METADATA_UNVERIFIED")
        fd = os.open(
            _EXIT_UNIT_PATH,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            with os.fdopen(os.dup(fd), "rb") as reader:
                data = reader.read(_MAX_UNIT_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) != info.st_size:
            raise RuntimeError("EXIT_UNIT_CHANGED_DURING_READ")
        return data

    def before(self) -> dict[str, tuple[int, int]]:
        state = self._show()
        if (
            state["ActiveState"] != "active"
            or state["SubState"] != "running"
            or self._unit_bytes() != self._old
        ):
            raise RuntimeError("EXIT_BASELINE_NOT_LIVE")
        before = _snapshot_units(self._sub_units)
        after = _snapshot_units(self._sub_units)
        if before != after:
            raise RuntimeError("EXIT_NEIGHBOR_BASELINE_MOVED")
        signed = _collect_credential_local_exit_snapshot()
        if signed.get("account_position_counts") != {"MAIN": 0, "SUB": 0}:
            # Specific first FLAT canary: an OPEN position requires a
            # separate actual restart/recovery proof, not blanket disarm.
            raise RuntimeError("EXIT_OPEN_POSITION_E2E_CANARY_NOT_WIRED")
        _check_flat_new_entry_inhibited()
        _validate_flat_exit_status(
            _read_protected_exit_status(),
            observed_at=datetime.now(UTC), not_before=None,
        )
        self._pre_signed_snapshot = signed
        return {k: v for k, v in before.items() if k != "EXIT"}

    def stop(self) -> None:
        self._cmd("stop", _EXIT_UNIT)

    def prove_stopped(self) -> None:
        s = self._show(allow_reload_pending=True)
        if (
            s["ActiveState"] != "inactive"
            or s["SubState"] != "dead"
            or s["MainPID"] != "0"
        ):
            raise RuntimeError("EXIT_STOPPED_PID_NOT_PROVEN")

    def install_unit(self, expected: bytes, replacement: bytes) -> None:
        """Atomic regular-file replacement; no shared symlink or other unit."""
        if expected not in (self._old, self._new):
            raise RuntimeError("EXIT_UNIT_PREIMAGE_UNTRUSTED")
        if replacement not in (self._old, self._new) or expected == replacement:
            raise RuntimeError("EXIT_UNIT_TARGET_UNTRUSTED")
        parent = _UNIT_PARENT.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != 0 or parent.st_gid != 0
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise RuntimeError("EXIT_UNIT_PARENT_UNTRUSTED")
        if self._unit_bytes() != expected:
            raise RuntimeError("EXIT_UNIT_PREIMAGE_CHANGED")
        if self._show(allow_reload_pending=True)["ActiveState"] != "inactive":
            raise RuntimeError("EXIT_UNIT_SWAP_REQUIRES_STOPPED")
        tmp = _UNIT_PARENT / (
            ".cripta-exit-only-" + _digest(replacement)[:20] + ".pending"
        )
        fd = os.open(
            tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        try:
            with os.fdopen(os.dup(fd), "wb") as writer:
                writer.write(replacement)
                writer.flush()
                os.fsync(writer.fileno())
            os.fchmod(fd, 0o644)
            os.fsync(fd)
        finally:
            os.close(fd)
        # Unknown outcome after replace is reconciled via unit digest/status.
        os.replace(tmp, _EXIT_UNIT_PATH)
        parent_fd = os.open(_UNIT_PARENT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        if self._unit_bytes() != replacement:
            raise RuntimeError("EXIT_UNIT_POST_SWAP_DIGEST_MISMATCH")

    def reload(self) -> None:
        self._cmd("daemon-reload")

    def start(self) -> None:
        self._restart_started_at = datetime.now(UTC)
        self._cmd("start", _EXIT_UNIT)

    def verify_new(self) -> None:
        s = self._show()
        if (
            self._unit_bytes() != self._new
            or s["ActiveState"] != "active"
            or s["SubState"] != "running"
            or int(s["MainPID"] or 0) <= 0
        ):
            raise RuntimeError("EXIT_NEW_UNIT_NOT_RUNNING")
        fields = (
            "Id", "ActiveState", "SubState", "MainPID",
            "NRestarts", "NeedDaemonReload", "WorkingDirectory",
        )
        actual = collect_exit_process_readback(
            systemd_show=self._cmd(
                "show", _EXIT_UNIT, "--no-pager",
                *("--property=" + key for key in fields),
            ),
            expected_commit=_COMPONENT,
            expected_source_commit=_SOURCE,
        )
        if actual.get("state") != "READ_ONLY_PROCESS_CONSISTENCY_ONLY":
            raise RuntimeError("EXIT_NEW_LOADED_PROCESS_NOT_PINNED")

    def verify_old(self) -> None:
        s = self._show()
        if (
            self._unit_bytes() != self._old
            or s["ActiveState"] != "active"
            or s["SubState"] != "running"
            or int(s["MainPID"] or 0) <= 0
        ):
            raise RuntimeError("EXIT_OLD_UNIT_NOT_RUNNING")

    def verify_signed_and_recovery(self) -> None:
        # The first protected canary only accepts actual disarmed FLAT.
        # OPEN requires a separately proven continuous Exit E2E contract.
        if self._pre_signed_snapshot is None or self._restart_started_at is None:
            raise RuntimeError("EXIT_PRECUTOVER_SIGNED_BASELINE_MISSING")
        current = _collect_credential_local_exit_snapshot()
        proof = _compare_recovery_snapshots(
            before=self._pre_signed_snapshot, after=current,
        )
        if (
            proof.get("state")
            != "SIGNED_EXISTING_POSITION_AND_PROTECTION_PRESERVED_READ_ONLY"
            or proof.get("account_position_counts") != {"MAIN": 0, "SUB": 0}
            or proof.get("signed_protection_snapshot_unchanged") is not True
            or proof.get("release_authorized") is not False
        ):
            raise RuntimeError("EXIT_SIGNED_PROTECTION_NOT_PRESERVED")
        _check_flat_new_entry_inhibited()
        deadline = time.monotonic() + 22
        while True:
            try:
                _validate_flat_exit_status(
                    _read_protected_exit_status(),
                    observed_at=datetime.now(UTC),
                    not_before=self._restart_started_at,
                )
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        # This method does not itself authorize a cutover or a LIVE ARM.

    def neighbors_unchanged(self, snapshot: object) -> None:
        after = {
            k: v for k, v in _snapshot_units(self._sub_units).items()
            if k != "EXIT"
        }
        if after != snapshot:
            raise RuntimeError("EXIT_NEIGHBOR_PID_OR_RESTART_DRIFT")

    def reconcile(self) -> str:
        try:
            unit = self._unit_bytes()
            s = self._show(allow_reload_pending=True)
        except Exception:
            return "UNKNOWN"
        if unit == self._old:
            prefix = "OLD"
        elif unit == self._new:
            prefix = "NEW"
        else:
            return "UNKNOWN"
        if (
            s["ActiveState"] == "active" and s["SubState"] == "running"
            and s["MainPID"].isdigit() and int(s["MainPID"]) > 0
        ):
            return prefix + "_ACTIVE"
        if (
            s["ActiveState"] == "inactive" and s["SubState"] == "dead"
            and s["MainPID"] == "0"
        ):
            return prefix + "_STOPPED"
        return "UNKNOWN"


class _RootAppendFsyncJournal:
    """Protected one-transaction durable journal, not a release authority.

    Requires a *preexisting* root:root0700 state directory and O_EXCL
    creation. No auto mkdir, path repair, reuse, truncation or log rotation.
    """
    def __init__(self) -> None:
        if sys.platform != "linux" or os.geteuid() != 0:
            _deny("ROOT_LINUX_REQUIRED")
        if (
            inspect_exit_journal_root_once().get("state")
            != "PROTECTED_EXIT_JOURNAL_ROOT_READY_READ_ONLY"
        ):
            _deny("JOURNAL_ROOT_UNTRUSTED_ANCESTRY")
        stat_dir = _JOURNAL_PARENT.lstat()
        if (
            not stat.S_ISDIR(stat_dir.st_mode)
            or stat_dir.st_uid != 0 or stat_dir.st_gid != 0
            or stat.S_IMODE(stat_dir.st_mode) != 0o700
        ):
            _deny("JOURNAL_PARENT_NOT_PROTECTED")
        self.path = _JOURNAL_PARENT / _JOURNAL_NAME
        self._fd = os.open(
            self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        parent_fd = os.open(_JOURNAL_PARENT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    def record(self, phase: str) -> None:
        if not isinstance(phase, str) or not phase.isascii() or not phase.replace(
            "_", ""
        ).isupper():
            _deny("JOURNAL_EVENT_INVALID")
        raw = (json.dumps({
            "time": datetime.now(UTC).isoformat(),
            "phase": phase,
            "unit": _EXIT_UNIT,
        }, separators=(",", ":"), sort_keys=True) + "\n").encode("ascii")
        if os.write(self._fd, raw) != len(raw):
            raise OSError("EXIT_JOURNAL_SHORT_WRITE")
        os.fsync(self._fd)

    def close(self) -> None:
        os.close(self._fd)


def _protected_real_admission() -> tuple[bytes, bytes]:
    """Root-controller sealed + protected OLD/NEW + fresh FLAT safety.

    This is a narrow first FLAT canary gate, NOT an OPEN-position block on
    independent release in general. OPEN requires separately tested owner.
    """
    _check_sealed_controller_root()
    old_unit, new_unit = _load_exact_exit_units()
    if not _unit_material_ok(old_unit, new_unit):
        _deny("OLD_AND_NEW_UNIT_NOT_DISTINCT")
    actual = _HostExitUnitIO(old_unit=old_unit, new_unit=new_unit)
    if actual._unit_bytes() != old_unit:
        _deny("LEGACY_EXIT_NOT_ACTIVE")
    before = actual._show()
    if (
        before["ActiveState"] != "active"
        or before["SubState"] != "running"
        or not before["MainPID"].isdigit()
        or int(before["MainPID"]) <= 0
    ):
        _deny("LEGACY_EXIT_NOT_RUNNING")
    _check_flat_new_entry_inhibited()
    signed = _collect_credential_local_exit_snapshot()
    if signed.get("account_position_counts") != {"MAIN": 0, "SUB": 0}:
        _deny("EXISTING_POSITION_REQUIRES_OPEN_OWNER_E2E")
    _validate_flat_exit_status(
        _read_protected_exit_status(),
        observed_at=datetime.now(UTC), not_before=None,
    )
    return old_unit, new_unit


def exit_only_cutover_once() -> dict[str, object]:
    """Root-only fixed ONE-SHOT scoped cutover, lock before any filesystem IO."""
    if sys.platform != "linux" or os.geteuid() != 0:
        _deny("ROOT_LINUX_REQUIRED")
    _check_sealed_controller_root()
    with _hold_shared_release_lock(path=_SHARED_LOCK, uid=0, gid=0):
        old_unit, new_unit = _protected_real_admission()
        journal = _RootAppendFsyncJournal()
        try:
            adapter = _HostExitUnitIO(old_unit=old_unit, new_unit=new_unit)
            return _run_exit_only_transition(
                adapter, journal, old_unit=old_unit, new_unit=new_unit,
                exercise_old_rollback=True,
            )
        finally:
            journal.close()


if __name__ == "__main__":
    if len(sys.argv) != 1 or Path(sys.argv[0]) != (
        _CONTROLLER_ROOT / "component_release_exit_transition.py"
    ):
        raise SystemExit("EXIT_ROOT_OWNER_NO_ARGUMENTS_REQUIRED")
    try:
        outcome = exit_only_cutover_once()
        print(json.dumps(outcome, sort_keys=True))
        if outcome["state"] not in (
            "EXIT_TRANSITION_ACCEPTANCE_PENDING",
            "EXIT_OLD_RECOVERED_AFTER_FAILURE",
        ):
            raise SystemExit(1)
    except (OSError, ValueError, RuntimeError):
        print("EXIT_ROOT_OWNER_BLOCKED_OR_OUTCOME_UNKNOWN")
        raise SystemExit(1) from None
