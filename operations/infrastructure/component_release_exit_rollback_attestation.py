"""#849 OLD Exit protected recovery artifact readback; strictly READ ONLY.

This gate verifies an already-staged, *not-installed* root-owned candidate.
It is deliberately not an Exit-only service deployment or rollback executor.
No systemd state, DB, Exchange, Owner ARM or credential mutation is possible.
The production wrapper accepts NO arguments and proves an exact fixed host
artifact against pinned independent SHA256, root ownership and unit bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from component_release_exit_legacy_prepare import (
    OLD_EXIT_COMMIT,
    _INPUT,
    _ISOLATED_VENV_FINGERPRINT,
    _OLD_UNIT_SHA,
    _ROOT,
    _SOURCE_ARCHIVE_SHA,
    _fingerprint_venv,
    _file_hash,
    _verify_legacy_fixture,
    build_pinned_old_exit_unit,
)
from component_release_exit_root_bootstrap import inspect_exit_root_tree

# Pinned from independent root readback on the real host, not trusted to
# a mutable manifest. Changing old bytes means a separate reviewed source.
_PROTECTED_OLD_PAYLOAD_SHA256 = (
    "0d2b9b8c6792609c9b594c5af7d006a4cf8ef6a5f5ef48565ab4369c5e635ec3"
)
_PROTECTED_OLD_UNIT_SHA256 = (
    "99b7ca07cae4a03b152510913e93f503e3e2e0c5f85b407a153796386ff73673"
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INPUT_FILES = (
    "legacy_runtime.tar", "legacy_unit.service", "INPUT_SNAPSHOT.json",
)
_CANDIDATE_MANIFEST = "OLD_EXIT_ROLLBACK_CANDIDATE.json"
_FALSE_FIELDS = (
    "rollback_ready",
    "systemd_unit_installed",
    "runtime_behavior_verified",
    "financial_mutation_authorized",
    "component_installed",
    "component_loaded",
    "signed_exchange_protection_verified",
)


def _deny(code: str) -> None:
    raise ValueError("OLD_EXIT_READBACK_" + code)


def _owner_mode(path: Path, uid: int, gid: int, mode: int, *, directory: bool) -> None:
    if path.is_symlink():
        _deny("UNTRUSTED_SYMLINK")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError("OLD_EXIT_READBACK_MISSING_PATH") from exc
    if (
        info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != mode
        or (
            not stat.S_ISDIR(info.st_mode) if directory
            else not stat.S_ISREG(info.st_mode)
        )
    ):
        _deny("OWNER_GROUP_MODE_OR_TYPE")


def _attest_old_exit_candidate_fixture(
    *,
    candidate_root: Path,
    rollback_parent: Path,
    protected_input_dir: Path,
    expected_payload_sha256: str,
    expected_archive_sha256: str,
    expected_venv_fingerprint: str,
    expected_pinned_unit_sha256: str,
    owner_uid: int,
    service_gid: int,
) -> dict[str, object]:
    """Data fixture inspection: tests may supply paths, NEVER a deploy permit.

    Even a positive synthetic result cannot authenticate the caller or
    certify the real host, service process, exchange or E2E recovery.
    The production wrapper pins paths and independent expected byte hashes.
    """
    if (
        not all(
            isinstance(x, Path) and x.is_absolute()
            for x in (candidate_root, rollback_parent, protected_input_dir)
        )
        or candidate_root != rollback_parent / OLD_EXIT_COMMIT
        or not all(
            isinstance(x, str) and _SHA256.fullmatch(x)
            for x in (
                expected_payload_sha256, expected_archive_sha256,
                expected_venv_fingerprint, expected_pinned_unit_sha256,
            )
        )
        or type(owner_uid) is not int or owner_uid < 0
        or type(service_gid) is not int or service_gid < 0
    ):
        _deny("INPUT_CONTRACT_INVALID")
    _owner_mode(
        rollback_parent, owner_uid, service_gid, 0o750, directory=True,
    )
    _owner_mode(
        protected_input_dir, owner_uid, service_gid, 0o750, directory=True,
    )
    for name in _INPUT_FILES:
        _owner_mode(
            protected_input_dir / name, owner_uid, service_gid, 0o640,
            directory=False,
        )

    original_tar = protected_input_dir / "legacy_runtime.tar"
    if _file_hash(original_tar, 64 * 1024 * 1024) != expected_archive_sha256:
        _deny("ORIGINAL_ARCHIVE_HASH")
    unit_path = protected_input_dir / "legacy_unit.service"
    if _file_hash(unit_path, 16 * 1024) != _OLD_UNIT_SHA:
        _deny("ORIGINAL_UNIT_HASH")

    original_unit = unit_path.read_bytes()
    pinned_unit = build_pinned_old_exit_unit(original_unit).encode("utf-8")
    if hashlib.sha256(pinned_unit).hexdigest() != expected_pinned_unit_sha256:
        _deny("PINNED_UNIT_EXPECTED_HASH")
    try:
        marker = json.loads(
            (protected_input_dir / "INPUT_SNAPSHOT.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("OLD_EXIT_READBACK_ORIGINAL_MARKER_UNREADABLE") from exc
    if (
        not isinstance(marker, dict)
        or marker.get("state")
        != "PROTECTED_LEGACY_EXIT_INPUT_ONLY_NOT_RECOVERABLE"
        or marker.get("source_commit") != OLD_EXIT_COMMIT
        or marker.get("archive_sha256") != expected_archive_sha256
        or marker.get("systemd_unit_sha256") != _OLD_UNIT_SHA
        or marker.get("rollback_ready") is not False
    ):
        _deny("ORIGINAL_MARKER_NOT_INPUT_ONLY")

    result = _verify_legacy_fixture(
        candidate_root=candidate_root,
        expected_archive_sha256=expected_archive_sha256,
        expected_venv_fingerprint=expected_venv_fingerprint,
        owner_uid=owner_uid,
        service_gid=service_gid,
    )
    if result["candidate_payload_sha256"] != expected_payload_sha256:
        _deny("INDEPENDENT_PAYLOAD_HASH")
    pinned_path = candidate_root / "legacy_exit_pinned.service"
    _owner_mode(
        pinned_path, owner_uid, service_gid, 0o640, directory=False,
    )
    if (
        _file_hash(pinned_path, 16 * 1024)
        != expected_pinned_unit_sha256
        or pinned_path.read_bytes() != pinned_unit
    ):
        _deny("PINNED_UNIT_NOT_EXACT_OLD")
    manifest_file = candidate_root / _CANDIDATE_MANIFEST
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("OLD_EXIT_READBACK_MANIFEST_UNREADABLE") from exc
    if not isinstance(manifest, dict) or any(
        manifest.get(flag) is not False for flag in _FALSE_FIELDS
    ):
        _deny("FORGED_READY_OR_FINANCIAL_STATE")
    venv = _fingerprint_venv(candidate_root / ".venv")
    if venv["symlink_count"] != 4 or venv["regular_file_count"] < 2:
        _deny("ISOLATED_PYTHON_MISSING")
    return {
        "state": "OLD_EXIT_RECOVERY_STAGED_READBACK_PASS",
        "component_id": "EXIT",
        "source_commit": OLD_EXIT_COMMIT,
        "candidate_root": str(candidate_root),
        "payload_sha256": expected_payload_sha256,
        "pinned_unit_sha256": expected_pinned_unit_sha256,
        "venv_regular_files": venv["regular_file_count"],
        "venv_symlink_count": venv["symlink_count"],
        "rollback_ready": False,
        "install_authorized": False,
        "systemd_unit_installed": False,
        "runtime_behavior_verified": False,
        "signed_main_sub_verified": False,
        "actual_host_attested": False,
        "financial_mutation_authorized": False,
    }


def attest_old_exit_candidate_on_host() -> dict[str, object]:
    """No-argument exact root READ ONLY production check, no service effect."""
    if os.geteuid() != 0:
        _deny("ROOT_REQUIRED")
    if inspect_exit_root_tree()["state"] != "ROOT_TREE_PREPARED_METADATA_ONLY":
        _deny("PROTECTED_ROOT_NOT_TRUSTED")
    import grp

    gid = grp.getgrnam("cripta").gr_gid
    result = _attest_old_exit_candidate_fixture(
        candidate_root=_ROOT / OLD_EXIT_COMMIT,
        rollback_parent=_ROOT,
        protected_input_dir=_INPUT,
        expected_payload_sha256=_PROTECTED_OLD_PAYLOAD_SHA256,
        expected_archive_sha256=_SOURCE_ARCHIVE_SHA,
        expected_venv_fingerprint=_ISOLATED_VENV_FINGERPRINT,
        expected_pinned_unit_sha256=_PROTECTED_OLD_UNIT_SHA256,
        owner_uid=0,
        service_gid=gid,
    )
    unit = _ROOT / OLD_EXIT_COMMIT / "legacy_exit_pinned.service"
    try:
        verification = subprocess.run(
            ["/usr/bin/systemd-analyze", "verify", str(unit)],
            check=False, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("OLD_EXIT_READBACK_SYSTEMD_SYNTAX_UNKNOWN") from exc
    if verification.returncode != 0 or verification.stdout or verification.stderr:
        _deny("SYSTEMD_SYNTAX_UNVERIFIED")
    return {
        **result,
        "actual_host_attested": True,
        "pinned_unit_systemd_syntax_verified": True,
        "rollback_ready": False,
        "runtime_behavior_verified": False,
        "signed_main_sub_verified": False,
    }
