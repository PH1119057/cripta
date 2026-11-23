"""Read-only snapshot consistency gate for a proposed isolated Exit release.

Issue #849 / ARCH §50.1 / DEV §50.6. This validates *supplied observations*,
not their provenance. No network, systemd, file I/O, database, process access,
installer, or financial permission is present. Even a valid synthetic snapshot
CANNOT authorize a production release or prove the actual loaded process.

The future privileged release owner must independently collect each observed
value from exact immutable files, systemd, /proc, and the running service,
then check signed protection, API/DB compatibility, and rollback separately.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_EXIT_UNIT = "cripta-universal-exit-consumer.service"
_RELEASE_PARENT = "/srv/cripta/components/exit/releases"
_EXIT_ENTRY = "operations/connectivity/universal_exit_consumer.py"
_EXIT_LOCK = "operations/infrastructure/exit_component_requirements.lock"
_FORBIDDEN_GLOBAL = "/srv/cripta/runtime/current"


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ValueError("EXIT_IDENTITY_" + code)


def _sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _exact_strings(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    return all(
        isinstance(k, str) and _digest(v)
        and not k.startswith("/")
        and not any(part in ("", ".", "..") for part in k.split("/"))
        and "\\" not in k
        for k, v in value.items()
    )


def _neighbors(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"ENTRY", "MARKET_SOURCE"}:
        return False
    return all(
        isinstance(row, (tuple, list))
        and len(row) == 2
        and type(row[0]) is int and row[0] > 0
        and type(row[1]) is int and row[1] >= 0
        for row in value.values()
    )



def collect_exit_installed_file_evidence(
    *,
    artifact_manifest: Mapping[str, Any],
    releases_parent: Path | None = None,
) -> dict[str, object]:
    """Read immutable candidate files + marker; never activate or write.

    The default root is the exact canonical component release parent.
    An explicit alternate parent exists for disposable CI fixtures only:
    the resulting evidence does not become production proof. This function
    is one non-atomic read-only observation, not a systemd/Bybit attestation.
    """
    _require(isinstance(artifact_manifest, Mapping), "ARTIFACT_INVALID")
    sha = artifact_manifest.get("target")
    _require(
        _sha(sha)
        and artifact_manifest.get("component") == "EXIT"
        and artifact_manifest.get("entrypoint") == _EXIT_ENTRY
        and artifact_manifest.get("dependency_lock") == _EXIT_LOCK
        and artifact_manifest.get("install_authorized") is False,
        "ARTIFACT_SCOPE_INVALID",
    )
    assert isinstance(sha, str)
    expected = artifact_manifest.get("blob_sha256")
    _require(_exact_strings(expected), "ARTIFACT_FILES_INVALID")
    assert isinstance(expected, Mapping)
    _require(
        _EXIT_ENTRY in expected and _EXIT_LOCK in expected
        and artifact_manifest.get("dependency_lock_sha256")
        == expected[_EXIT_LOCK],
        "ARTIFACT_LOCK_INVALID",
    )
    parent = (
        Path(_RELEASE_PARENT) if releases_parent is None else releases_parent
    )
    _require(
        isinstance(parent, Path) and parent.is_absolute(),
        "RELEASE_PARENT_UNTRUSTED",
    )
    root = parent / sha
    _require(
        parent.is_dir() and not parent.is_symlink()
        and root.is_dir() and not root.is_symlink()
        # Only Linux is an executable target. Windows CI temp paths may
        # normalize case/reparse aliases; direct symlinks remain forbidden.
        and (os.name == "nt" or root.resolve() == root),
        "RELEASE_NOT_INSTALLED",
    )
    installed_marker = root / "COMPONENT_INSTALLED_COMMIT"
    _require(
        installed_marker.is_file() and not installed_marker.is_symlink(),
        "INSTALLED_MARKER_MISSING",
    )
    try:
        marker_stat = installed_marker.stat()
        _require(stat.S_ISREG(marker_stat.st_mode), "INSTALLED_MARKER_INVALID")
        _require(
            installed_marker.read_bytes() == (sha + "\n").encode("ascii"),
            "INSTALLED_MARKER_MISMATCH",
        )
        actual: dict[str, str] = {}
        for relative, pinned in expected.items():
            _require(isinstance(relative, str), "INSTALLED_PATH_INVALID")
            directory = root
            parts = PurePosixPath(relative).parts
            for name in parts[:-1]:
                directory = directory / name
                _require(
                    directory.is_dir() and not directory.is_symlink(),
                    "INSTALLED_DIRECTORY_UNTRUSTED",
                )
            file = root.joinpath(*parts)
            _require(
                file.is_file() and not file.is_symlink(),
                "INSTALLED_FILE_UNTRUSTED",
            )
            info = file.stat()
            _require(
                stat.S_ISREG(info.st_mode) and info.st_size <= 128 * 1024 * 1024,
                "INSTALLED_FILE_INVALID",
            )
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            _require(digest == pinned, "INSTALLED_FILE_HASH_MISMATCH")
            actual[relative] = digest
    except OSError as exc:
        raise ValueError("EXIT_IDENTITY_INSTALLED_READ_FAILED") from exc
    return {
        "installed_root": str(root),
        "installed_marker": sha,
        "installed_file_sha256": actual,
        "installed_lock_sha256": actual[_EXIT_LOCK],
        "installed_files_regular": True,
        "canonical_release_parent": parent == Path(_RELEASE_PARENT),
        "install_authorized": False,
        "runtime_behavior_verified": False,
    }


def check_exit_identity_snapshot(
    *,
    artifact_manifest: Mapping[str, Any],
    proposed_unit: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> dict[str, object]:
    """Strictly check internally consistent metadata; NOT an install permit.

    `process_executable` is the captured process argv[0] from /proc/cmdline,
    *not* the kernel /proc/<pid>/exe realpath (a venv's python may be a link
    to the OS interpreter). `process_entrypoint` is captured argv[1].
    A trusted host collector and the full ABI/Exchange/protection gates are
    prerequisites to making any actual runtime/financial claim.
    """
    _require(
        isinstance(artifact_manifest, Mapping)
        and isinstance(proposed_unit, Mapping)
        and isinstance(observed, Mapping),
        "OBSERVATIONS_INVALID",
    )
    sha = artifact_manifest.get("target")
    _require(_sha(sha), "CANDIDATE_SHA_INVALID")
    assert isinstance(sha, str)
    root = _RELEASE_PARENT + "/" + sha
    _require(
        artifact_manifest.get("component") == "EXIT"
        and artifact_manifest.get("entrypoint") == _EXIT_ENTRY
        and artifact_manifest.get("dependency_lock") == _EXIT_LOCK
        and artifact_manifest.get("status") == "STAGED_ONLY_NOT_DEPLOYABLE"
        and artifact_manifest.get("install_authorized") is False,
        "ARTIFACT_SCOPE_INVALID",
    )
    expected_files = artifact_manifest.get("blob_sha256")
    _require(_exact_strings(expected_files), "ARTIFACT_FILES_INVALID")
    assert isinstance(expected_files, Mapping)
    _require(
        _EXIT_ENTRY in expected_files and _EXIT_LOCK in expected_files
        and _digest(artifact_manifest.get("dependency_lock_sha256"))
        and artifact_manifest.get("dependency_lock_sha256")
        == expected_files[_EXIT_LOCK],
        "ARTIFACT_LOCK_INVALID",
    )

    source_compat = proposed_unit.get("source_compatibility_commit")
    _require(_sha(source_compat), "SOURCE_COMPATIBILITY_IDENTITY_INVALID")
    _require(
        proposed_unit.get("component_id") == "EXIT"
        and proposed_unit.get("unit_instance") == _EXIT_UNIT
        and proposed_unit.get("source_commit") == sha
        and proposed_unit.get("component_release_root") == root
        and proposed_unit.get("component_lock_sha256")
        == artifact_manifest.get("dependency_lock_sha256")
        and proposed_unit.get("status") == "STAGED_ONLY_NOT_DEPLOYABLE"
        and proposed_unit.get("install_authorized") is False,
        "UNIT_CANDIDATE_MISMATCH",
    )
    unit_text = proposed_unit.get("unit_text")
    unit_hash = proposed_unit.get("unit_sha256")
    _require(
        isinstance(unit_text, str) and _digest(unit_hash)
        and hashlib.sha256(unit_text.encode("utf-8")).hexdigest() == unit_hash,
        "UNIT_DIGEST_MISMATCH",
    )
    assert isinstance(unit_text, str)
    lines = unit_text.splitlines()
    pinned = (
        "WorkingDirectory=" + root,
        "Environment=CRIPTA_RELEASE_COMMIT=" + str(source_compat),
        "Environment=CRIPTA_EXIT_COMPONENT_COMMIT=" + sha,
        "Environment=CRIPTA_RUNTIME_ROOT=" + root,
        "Environment=PYTHONPATH=" + root + "/src:" + root
        + "/operations/connectivity",
        "ExecStart=" + root + "/.venv/bin/python " + root + "/" + _EXIT_ENTRY,
        "User=cripta",
        "Group=cripta",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "EnvironmentFile=-/etc/cripta/universal_exit_consumer.env",
    )
    _require(
        all(lines.count(x) == 1 for x in pinned)
        and _FORBIDDEN_GLOBAL not in unit_text
        and "EnvironmentFile=-/etc/cripta/release.env" not in unit_text,
        "UNIT_PINNING_UNVERIFIED",
    )

    _require(
        observed.get("installed_root") == root
        and observed.get("installed_marker") == sha
        and observed.get("installed_lock_sha256")
        == artifact_manifest.get("dependency_lock_sha256")
        and observed.get("installed_files_regular") is True,
        "INSTALLED_IDENTITY_MISMATCH",
    )
    installed_files = observed.get("installed_file_sha256")
    _require(
        _exact_strings(installed_files)
        and dict(installed_files) == dict(expected_files),
        "INSTALLED_BLOB_DRIFT",
    )
    _require(
        observed.get("unit_name") == _EXIT_UNIT
        and observed.get("unit_sha256") == unit_hash
        and observed.get("systemd_active_state") == "active"
        and observed.get("systemd_sub_state") == "running",
        "LOADED_UNIT_IDENTITY_INVALID",
    )
    _require(
        type(observed.get("pid")) is int and observed["pid"] > 0
        and type(observed.get("pid_start_ticks")) is int
        and observed["pid_start_ticks"] > 0,
        "PROCESS_IDENTITY_INVALID",
    )
    _require(
        observed.get("process_unit") == _EXIT_UNIT
        and observed.get("process_executable") == root + "/.venv/bin/python"
        and observed.get("process_entrypoint") == root + "/" + _EXIT_ENTRY
        and observed.get("process_working_directory") == root
        and observed.get("process_pythonpath") == root + "/src:" + root
        + "/operations/connectivity"
        and observed.get("loaded_release_commit") == source_compat
        and observed.get("loaded_exit_component_commit") == sha
        and observed.get("loaded_runtime_root") == root
        and observed.get("observed_executable_under_installed_root") is True,
        "PROCESS_RELEASE_NOT_PINNED",
    )
    _require(
        _sha(observed.get("rollback_commit"))
        and observed["rollback_commit"] != sha
        and observed.get("rollback_immutable_ready") is True,
        "ROLLBACK_EVIDENCE_INCOMPLETE",
    )
    before, after = observed.get("neighbor_before"), observed.get("neighbor_after")
    _require(
        _neighbors(before) and _neighbors(after) and before == after,
        "NEIGHBOR_PID_DRIFT",
    )
    # Caller-supplied observations may be fabricated, stale, or from a
    # disposable fixture. Never elevate metadata equality to live behavior.
    return {
        "state": "OFFLINE_METADATA_CONSISTENCY_ONLY",
        "component_id": "EXIT",
        "component_commit": sha,
        "source_compatibility_commit": source_compat,
        "installed_component_marker_consistent": True,
        "loaded_component_identity_consistent": True,
        "untouched_neighbor_snapshot_consistent": True,
        "install_authorized": False,
        "runtime_behavior_verified": False,
        "live_financial_gate_verified": False,
        "exchange_protection_verified": False,
    }
