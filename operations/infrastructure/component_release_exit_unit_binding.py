"""#849: immutable independently attested Exit unit binding, NO service action.

The previously sealed b451 component payload remains byte-for-byte unchanged.
This new metadata binds exactly one corrected, source-compatible systemd
unit to that candidate. No root writer may silently rewrite its original
preparation record. Only the future separately gated, no-arg Exit-only
transaction may select this distinct binding after validating source/position
compatibility, actual OLD rollback, and signed MAIN/SUB recovery.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from component_release_exit_legacy_prepare import OLD_EXIT_COMMIT
from component_release_exit_new_install import (
    _readback_prepared_candidate,
)
from component_release_exit_owner_preflight import (
    _ORIGINAL_UNIT_SHA256,
    _SUB_UNIT,
    _installed_unit_sha256,
    _snapshot_units,
    _sub_private_units,
)
from component_release_exit_rollback_attestation import (
    attest_old_exit_candidate_on_host,
)
from component_release_exit_root_bootstrap import (
    _SHARED_LOCK,
    _hold_shared_release_lock,
    inspect_exit_root_tree,
)
from component_release_exit_unit_spec import build_exit_unit_candidate

_COMPONENT = "b451165846af286e2f31404d9cdb4eb5cf599031"
_SOURCE = OLD_EXIT_COMMIT
_ROOT = Path("/srv/cripta/components/exit/releases")
_COMPONENT_ROOT = _ROOT / _COMPONENT
_BINDING_NAME = ".unit-binding-" + _COMPONENT
_UNIT_FILE = "pinned_exit_corrected.service"
_MARKER_FILE = "UNIT_BINDING_PREPARED.json"
_EXIT_UNIT = Path("/etc/systemd/system/cripta-universal-exit-consumer.service")
_ARCHIVE_SHA256 = "2193d32f24f0f2980a7678e844a93526cf5963f1c0bdaa36d4a74660c42b29d7"
_PAYLOAD_SHA256 = "658b74091c757e3fccb717f6622824ea5280586bc16d75eeb435b9ca50b9ac1d"
_UNIT_SHA256 = "d3da5b8614887471ba2b1681513b9d837dbc5ec2ce1dbe8676f561afb6a4c142"
_LOCK_SHA256 = "eb9a7aea12c9be5836b32c2c18570adf395f0512d3c3f1bff10a1853d3ef6c15"


def _deny(code: str) -> None:
    raise ValueError("EXIT_BINDING_" + code)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_new_regular(root: Path, name: str, raw: bytes) -> None:
    path = root / name
    with path.open("xb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def _check_root_directory(path: Path, uid: int, gid: int, mode: int) -> None:
    if path.is_symlink() or not path.is_dir():
        _deny("ROOT_PATH_INVALID")
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        != (uid, gid, mode)
    ):
        _deny("ROOT_OWNER_MODE_INVALID")


def _check_regular(
    path: Path, *, uid: int, gid: int, mode: int,
    expected_digest: str, max_size: int,
) -> None:
    z = path.lstat()
    if (
        not stat.S_ISREG(z.st_mode) or z.st_nlink != 1
        or (z.st_uid, z.st_gid, stat.S_IMODE(z.st_mode)) != (uid, gid, mode)
        or not 0 < z.st_size <= max_size
        or path.is_symlink()
    ):
        _deny("BOUND_FILE_OWNER_MODE_INVALID")
    with path.open("rb") as stream:
        data = stream.read(max_size + 1)
    if len(data) != z.st_size or _sha(data) != expected_digest:
        _deny("BOUND_FILE_DIGEST_INVALID")


def _check_prepared_component_readback(candidate: object) -> None:
    """Match the actual NEW stage readback, not an invented component key."""
    if (
        not isinstance(candidate, dict)
        or candidate.get("state") != "IMMUTABLE_EXIT_CANDIDATE_PREPARED"
        or candidate.get("component_id") != "EXIT"
        or candidate.get("source_commit") != _COMPONENT
        or candidate.get("payload_sha256") != _PAYLOAD_SHA256
        or candidate.get("physical_file_hashes_verified") is not True
        or candidate.get("new_component_loaded") is not False
        or candidate.get("rollback_ready") is not False
        or candidate.get("cutover_authorized") is not False
        or candidate.get("financial_mutation_authorized") is not False
    ):
        _deny("NEW_COMPONENT_ROOT_NOT_ATTESTED")


def _binding_record() -> dict[str, object]:
    return {
        "schema": "CRIPTA_EXIT_SOURCE_COMPATIBLE_UNIT_BINDING_V1",
        "component": "EXIT",
        "component_source_commit": _COMPONENT,
        "source_epoch_compatibility_commit": _SOURCE,
        "source_abi_digest": "bf466358dae62ed940c0b7972c9397c57191a51fa15a9ea618bf27f1a9c459a8",
        "component_root": str(_COMPONENT_ROOT),
        "component_payload_sha256": _PAYLOAD_SHA256,
        "component_archive_sha256": _ARCHIVE_SHA256,
        "component_lock_sha256": _LOCK_SHA256,
        "original_installed_unit_sha256": _ORIGINAL_UNIT_SHA256,
        "corrected_unit_sha256": _UNIT_SHA256,
        "state": "PROTECTED_EXIT_UNIT_BINDING_PREPARED_ONLY",
        "component_installed": False,
        "component_loaded": False,
        "runtime_behavior_verified": False,
        "source_epoch_registration_verified": False,
        "rollback_ready": False,
        "cutover_authorized": False,
        "financial_mutation_authorized": False,
    }


def _readback_binding_fixture(
    *, parent: Path, owner_uid: int, service_gid: int,
    binding_name: str, expected_unit: bytes,
) -> dict[str, object]:
    """Private filesystem readback, NOT an admission / deployment grant."""
    _check_root_directory(parent, owner_uid, service_gid, 0o750)
    binding = parent / binding_name
    _check_root_directory(binding, owner_uid, service_gid, 0o750)
    files = tuple(sorted(item.name for item in binding.iterdir()))
    if files != (_MARKER_FILE, _UNIT_FILE):
        _deny("BOUND_FILE_SET_INVALID")
    marker = binding / _MARKER_FILE
    unit = binding / _UNIT_FILE
    expected_record = _binding_record()
    record_raw = (json.dumps(
        expected_record, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("ascii")
    _check_regular(
        marker, uid=owner_uid, gid=service_gid, mode=0o640,
        expected_digest=_sha(record_raw), max_size=4096,
    )
    _check_regular(
        unit, uid=owner_uid, gid=service_gid, mode=0o640,
        expected_digest=_sha(expected_unit), max_size=16 * 1024,
    )
    if _sha(expected_unit) != _UNIT_SHA256:
        _deny("EXPECTED_PINNED_UNIT_DRIFT")
    return {
        "state": "PROTECTED_EXIT_UNIT_BINDING_PREPARED_ONLY",
        "binding_root": str(binding),
        "component_commit": _COMPONENT,
        "source_compatibility_commit": _SOURCE,
        "sealed_component_payload_sha256": _PAYLOAD_SHA256,
        "corrected_unit_sha256": _UNIT_SHA256,
        "file_digest_readback": True,
        "component_loaded": False,
        "source_epoch_registration_verified": False,
        "rollback_ready": False,
        "cutover_authorized": False,
        "runtime_behavior_verified": False,
        "financial_mutation_authorized": False,
        "systemd_action_performed": False,
    }


def _stage_binding_fixture(
    *, parent: Path, owner_uid: int, service_gid: int,
    binding_name: str, expected_unit: bytes,
) -> dict[str, object]:
    """Private test adapter; runtime only calls fixed root-owned owner below."""
    _check_root_directory(parent, owner_uid, service_gid, 0o750)
    if _sha(expected_unit) != _UNIT_SHA256:
        _deny("EXPECTED_PINNED_UNIT_DRIFT")
    binding = parent / binding_name
    pending = parent / (".pending-" + binding_name.lstrip("."))
    if (
        binding.exists() or binding.is_symlink()
        or pending.exists() or pending.is_symlink()
    ):
        _deny("BINDING_OR_PARTIAL_STAGE_ALREADY_EXISTS")
    pending.mkdir(mode=0o700)
    _write_new_regular(pending, _UNIT_FILE, expected_unit)
    record = (json.dumps(
        _binding_record(), sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("ascii")
    _write_new_regular(pending, _MARKER_FILE, record)
    for name in (_UNIT_FILE, _MARKER_FILE):
        child = pending / name
        os.chown(child, owner_uid, service_gid, follow_symlinks=False)
        os.chmod(child, 0o640)
    os.chown(pending, owner_uid, service_gid, follow_symlinks=False)
    os.chmod(pending, 0o750)
    fd = os.open(pending, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(pending, binding)
    fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return _readback_binding_fixture(
        parent=parent, owner_uid=owner_uid, service_gid=service_gid,
        binding_name=binding_name, expected_unit=expected_unit,
    )


def prepare_exit_source_compatible_unit_once() -> dict[str, object]:
    """Root-only NO-ARGUMENT sealed UNIT binding, no systemd or SQL operation."""
    if sys.platform != "linux" or os.geteuid() != 0:
        _deny("ROOT_LINUX_REQUIRED")
    import grp

    group = grp.getgrnam("cripta").gr_gid
    with _hold_shared_release_lock(path=_SHARED_LOCK, uid=0, gid=0):
        if inspect_exit_root_tree()["state"] != "ROOT_TREE_PREPARED_METADATA_ONLY":
            _deny("PROTECTED_COMPONENT_TREE_INVALID")
        old = attest_old_exit_candidate_on_host()
        if (
            old.get("state") != "OLD_EXIT_RECOVERY_STAGED_READBACK_PASS"
            or old.get("actual_host_attested") is not True
        ):
            _deny("OLD_ROLLBACK_ARTIFACT_NOT_ATTESTED")
        new = _readback_prepared_candidate(
            candidate_root=_COMPONENT_ROOT,
            owner_uid=0, service_gid=group,
            expected_commit=_COMPONENT,
            expected_archive_sha256=_ARCHIVE_SHA256,
        )
        _check_prepared_component_readback(new)
        if _installed_unit_sha256() != _ORIGINAL_UNIT_SHA256:
            _deny("OLD_ACTIVE_UNIT_DRIFT")
        marker = Path("/var/lib/cripta/release/INSTALLED_COMMIT")
        if marker.is_symlink() or marker.read_text("ascii").strip() != _SOURCE:
            _deny("LEGACY_SOURCE_RELEASE_DRIFT")
        sub_units = _sub_private_units()
        before = _snapshot_units(sub_units)
        if before != _snapshot_units(sub_units):
            _deny("NEIGHBOR_SNAPSHOT_NOT_STABLE")
        original = _EXIT_UNIT.read_bytes()
        if _sha(original) != _ORIGINAL_UNIT_SHA256:
            _deny("LEGACY_UNIT_BYTES_CHANGED")
        proposal = build_exit_unit_candidate(
            source_unit=original.decode("utf-8"),
            component_commit=_COMPONENT,
            component_lock_sha256=_LOCK_SHA256,
            source_compatibility_commit=_SOURCE,
        )
        unit = proposal["unit_text"].encode("utf-8")
        if _sha(unit) != _UNIT_SHA256:
            _deny("PINNED_NEW_UNIT_CHANGED")
        result = _stage_binding_fixture(
            parent=_ROOT, owner_uid=0, service_gid=group,
            binding_name=_BINDING_NAME, expected_unit=unit,
        )
        if before != _snapshot_units(sub_units):
            _deny("NEIGHBOR_CHANGED_AFTER_BINDING_PREPARE")
        if _installed_unit_sha256() != _ORIGINAL_UNIT_SHA256:
            _deny("ORIGINAL_EXIT_UNIT_CHANGED_DURING_PREPARE")
        return {
            **result,
            "root_protected_actor_verified": True,
            "shared_release_lock_verified": True,
            "neighbor_pid_snapshot_stable": True,
            "original_component_payload_unchanged": True,
        }


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("EXIT_UNIT_BINDING_NO_ARGUMENTS")
    try:
        print(json.dumps(
            prepare_exit_source_compatible_unit_once(), sort_keys=True,
        ))
    except (OSError, ValueError):
        print("EXIT_UNIT_BINDING_BLOCKED")
        raise SystemExit(1) from None
