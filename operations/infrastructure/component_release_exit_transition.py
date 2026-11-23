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
from datetime import UTC, datetime
from pathlib import Path

from component_release_exit_owner_preflight import (
    _EXIT_UNIT,
    _EXIT_UNIT_PATH,
    _read_unit_state,
    _snapshot_units,
    _sub_private_units,
)

_UNIT_PARENT = Path("/etc/systemd/system")
_JOURNAL_PARENT = Path("/var/lib/cripta/release/exit_transactions")
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
        # No new service acceptance without independent current credential-
        # local signed MAIN/SUB, position recovery and the ExitPlan owner.
        raise RuntimeError("EXIT_CURRENT_POSITION_E2E_PROOF_NOT_WIRED")

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


def _protected_real_admission() -> None:
    """No LIVE grant without all independent source+Exchange+rollback gates."""
    # The complete verified, no-argument admission collector is not installed.
    # Deliberately do not activate a financial Exit from a Draft PR.
    _deny("RELEASE_NOT_MERGED_OR_PROTECTION_NOT_VERIFIED")


def exit_only_cutover_once() -> dict[str, object]:
    """Fixed NO-ARGUMENT public entrypoint. NEVER permit synthetic evidence."""
    if sys.platform != "linux":
        _deny("ROOT_LINUX_REQUIRED")
    _protected_real_admission()
    # No host actuator is constructed while admission is incomplete.
    _deny("GATED_ROOT_OWNER_NOT_INSTALLED")
