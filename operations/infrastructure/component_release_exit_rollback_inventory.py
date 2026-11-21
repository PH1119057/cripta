"""Read-only inventory of OLD mutable Exit inputs, never rollback proof.

#849 Phase2m. The current legacy global release can be written by its
service actor. A hash of its current bytes is NOT a protected rollback and
must NEVER be treated as permission to stop, restart or re-arm a service.

This module never copies files or reads environment secret values, makes
network/DB/Bybit calls, creates a rollback marker or invokes systemd.
The private alternate-path adapter is for disposable fixtures only.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RUNTIME_RELEASES = Path("/srv/cripta/runtime/releases")
_CURRENT = Path("/srv/cripta/runtime/current")
_UNIT_PATH = Path("/etc/systemd/system/cripta-universal-exit-consumer.service")
_UNIT_HASH = "c4a21004e637df794670d7d096c6ffa443c23a881c662b65bebfc11459433248"
_ENTRY = "operations/connectivity/universal_exit_consumer.py"
_REQUIRED_FILES = frozenset({
    _ENTRY,
    "src/bybit_workbench/universal_exit/p90l90_real_v3.py",
    ".venv/pyvenv.cfg",
})
_SURFACES = ("src", "operations/connectivity", ".venv")
_VENV_LINKS = {
    ".venv/lib64": "lib",
    ".venv/bin/python": "python3",
    ".venv/bin/python3.12": "python3",
    ".venv/bin/python3": "/usr/bin/python3",
}
_MAX_FILES = 6000
_MAX_TREE_BYTES = 512 * 1024 * 1024
_MAX_ONE_FILE = 128 * 1024 * 1024


def _deny(code: str) -> None:
    raise ValueError("EXIT_ROLLBACK_INPUT_" + code)


def _stat_dir(item: Path, owner: int, *, root: bool = False) -> os.stat_result:
    if item.is_symlink():
        _deny("DIRECTORY_SYMLINK")
    try:
        info = item.lstat()
    except OSError as exc:
        raise ValueError("EXIT_ROLLBACK_INPUT_DIRECTORY_MISSING") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner:
        _deny("DIRECTORY_OWNER_OR_TYPE_INVALID")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022 or (root and mode != 0o750):
        _deny("DIRECTORY_MODE_INVALID")
    return info


def _regular_hash(
    item: Path, owner: int, *, max_size: int = _MAX_ONE_FILE,
) -> tuple[str, int, int]:
    try:
        info = item.lstat()
    except OSError as exc:
        raise ValueError("EXIT_ROLLBACK_INPUT_REQUIRED_FILE_UNREADABLE") from exc
    if stat.S_ISLNK(info.st_mode):
        _deny("REGULAR_FILE_SYMLINK")
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != owner
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        _deny("REGULAR_FILE_OWNER_MODE_OR_TYPE_INVALID")
    if info.st_size > max_size:
        _deny("REGULAR_FILE_OVERSIZE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(item, flags)
        try:
            opened = os.fstat(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != info.st_uid
                or opened.st_ino != info.st_ino
                or opened.st_size != info.st_size
                or opened.st_dev != info.st_dev
            ):
                _deny("REGULAR_FILE_REPLACED_DURING_READ")
            digest = hashlib.sha256()
            size = 0
            while True:
                block = os.read(fd, 128 * 1024)
                if not block:
                    break
                size += len(block)
                if size > max_size:
                    _deny("REGULAR_FILE_OVERSIZE")
                digest.update(block)
            after = os.fstat(fd)
            if (
                size != info.st_size or after.st_size != info.st_size
                or after.st_mtime_ns != info.st_mtime_ns
            ):
                _deny("REGULAR_FILE_CHANGED_DURING_READ")
            return digest.hexdigest(), size, stat.S_IMODE(info.st_mode)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("EXIT_ROLLBACK_INPUT_FILE_READ_FAILED") from exc


def _audit_legacy_exit_input(
    *,
    release: Path,
    unit: Path | None,
    expected_sha: str,
    release_owner_uid: int,
    unit_owner_uid: int | None,
) -> dict[str, object]:
    """Metadata+bytes snapshot of mutable inputs, NOT physical backup.

    Alternate paths and UIDs exist only for Linux disposable unit fixtures.
    Production calls the fixed no-argument wrapper below.
    """
    if (
        not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha)
        or not isinstance(release, Path) or not release.is_absolute()
        or (unit is not None and (
            not isinstance(unit, Path) or not unit.is_absolute()
            or type(unit_owner_uid) is not int
        ))
        or release.name != expected_sha
    ):
        _deny("BASELINE_IDENTITY_INVALID")
    info = _stat_dir(release, release_owner_uid, root=True)
    if not stat.S_ISDIR(info.st_mode):
        _deny("BASELINE_DIR_INVALID")
    # The legacy unit's parent /etc/systemd/system is 0700 root-owned.
    # UID107 cannot read it. Actual runtime inventory MUST remain possible
    # without pretending caller-supplied unit bytes are privileged proof.
    unit_sha: str | None = None
    if unit is not None:
        assert isinstance(unit_owner_uid, int)
        unit_sha, unit_size, _ = _regular_hash(unit, unit_owner_uid)
        if unit_sha != _UNIT_HASH or unit_size < 100:
            _deny("UNIT_HASH_UNAPPROVED")
    marker_sha, marker_size, _ = _regular_hash(
        release / "INSTALLED_COMMIT", release_owner_uid, max_size=256,
    )
    if marker_size != 41 or marker_sha != hashlib.sha256(
        (expected_sha + "\n").encode("ascii")
    ).hexdigest():
        _deny("INSTALLED_COMMIT_MISMATCH")
    for name in _SURFACES:
        surface = release / name
        surface_stat = _stat_dir(surface, release_owner_uid)
        if surface_stat.st_dev != info.st_dev:
            _deny("UNEXPECTED_MOUNT")
    hashed: list[tuple[str, str, int, int, str]] = []
    link_count = 0
    file_count = 0
    byte_count = 0
    excluded_cache_dirs = 0
    excluded_mutable_cache_dirs = 0
    regular_seen: set[str] = set()
    link_seen: set[str] = set()
    for base in _SURFACES:
        subtree = release / base
        for directory, names, files in os.walk(subtree, followlinks=False):
            # __pycache__ is runtime-generated bytecode, not reviewed Git
            # source or an immutable rollback artifact. On the real host
            # prior root-owned diagnostic work left 15 root:root cache
            # directories and 94 .pyc files. Exclude only real,
            # non-symlink cache directories; NEVER chown/delete them.
            if "__pycache__" in names:
                cache = Path(directory) / "__pycache__"
                meta = cache.lstat()
                if stat.S_ISLNK(meta.st_mode):
                    _deny("GENERATED_CACHE_SYMLINK_UNTRUSTED")
                cache_mode = stat.S_IMODE(meta.st_mode)
                accepted_cache_mode = (
                    (meta.st_uid == 0 and cache_mode == 0o755)
                    or (meta.st_uid == release_owner_uid
                        and cache_mode in (0o755, 0o775))
                )
                if (
                    not stat.S_ISDIR(meta.st_mode)
                    or meta.st_dev != info.st_dev
                    or not accepted_cache_mode
                ):
                    _deny("GENERATED_CACHE_OWNER_MODE_OR_MOUNT_INVALID")
                names.remove("__pycache__")
                excluded_cache_dirs += 1
                if cache_mode == 0o775:
                    excluded_mutable_cache_dirs += 1
            for name in sorted(names + files):
                item = Path(directory) / name
                relative = item.relative_to(release).as_posix()
                try:
                    mode_info = item.lstat()
                except OSError as exc:
                    raise ValueError("EXIT_ROLLBACK_INPUT_WALK_FAILED") from exc
                if mode_info.st_dev != info.st_dev:
                    _deny("UNEXPECTED_MOUNT")
                if stat.S_ISLNK(mode_info.st_mode):
                    if relative not in _VENV_LINKS:
                        _deny("UNEXPECTED_SYMLINK")
                    if os.readlink(item) != _VENV_LINKS[relative]:
                        _deny("SYMLINK_TARGET_INVALID")
                    link_count += 1
                    link_seen.add(relative)
                    hashed.append(("L", relative, 0, 0, _VENV_LINKS[relative]))
                elif stat.S_ISDIR(mode_info.st_mode):
                    _stat_dir(item, release_owner_uid)
                    hashed.append(
                        ("D", relative, stat.S_IMODE(mode_info.st_mode), 0, "")
                    )
                elif stat.S_ISREG(mode_info.st_mode):
                    digest, size, mode = _regular_hash(item, release_owner_uid)
                    byte_count += size
                    file_count += 1
                    regular_seen.add(relative)
                    hashed.append(("F", relative, mode, size, digest))
                else:
                    _deny("SPECIAL_FILE_UNTRUSTED")
                if len(hashed) > _MAX_FILES or byte_count > _MAX_TREE_BYTES:
                    _deny("TREE_BOUNDS_EXCEEDED")
    if link_seen != set(_VENV_LINKS):
        _deny("REQUIRED_PYTHON_SYMLINKS_MISSING")
    if not _REQUIRED_FILES <= regular_seen:
        _deny("REQUIRED_EXECUTABLE_CLOSURE_MISSING")
    fingerprint = hashlib.sha256()
    for kind, relative, mode, size, digest in sorted(hashed):
        fingerprint.update(
            f"{kind}\0{relative}\0{mode:o}\0{size}\0{digest}\n".encode("utf-8")
        )
    return {
        "state": (
            "LEGACY_ROLLBACK_INPUT_ONLY_UNPROTECTED" if unit is not None
            else "LEGACY_ROLLBACK_INPUT_REQUIRES_ROOT_UNIT_READBACK"
        ),
        "previous_commit": expected_sha,
        "legacy_source_root": str(release),
        "legacy_release_owner_uid": release_owner_uid,
        "legacy_release_mutable_by_runtime_actor": True,
        "unit_sha256": unit_sha,
        "unit_readback_required_from_root": unit is None,
        "unit_readback_independently_attested": False,
        "entrypoint": _ENTRY,
        "regular_file_count": file_count,
        "symlink_count": link_count,
        "regular_file_bytes": byte_count,
        "excluded_bytecode_cache_directory_count": excluded_cache_dirs,
        "excluded_mutable_cache_directory_count": excluded_mutable_cache_dirs,
        "bytecode_cache_excluded_not_backup_attested": True,
        "input_tree_sha256": fingerprint.hexdigest(),
        "inputs_read_only": True,
        "immutable_rollback_ready": False,
        "previous_python_isolated": False,
        "previous_dependency_abi_attested": False,
        "previous_unit_pinned": False,
        "install_authorized": False,
        "runtime_behavior_verified": False,
        "signed_exchange_protection_verified": False,
        "financial_mutation_authorized": False,
    }


def inspect_previous_exit_rollback_inputs() -> dict[str, object]:
    """Read actual legacy Exit source and unit, never copy or install.

    Only a future reviewed root owner may copy the complete immutable
    runtime and unit, attest preserved dependencies, then stage rollback.
    """
    import pwd  # Linux target only; fixture and Windows CI do not invoke.

    uid = pwd.getpwnam("cripta").pw_uid
    try:
        release = _CURRENT.resolve(strict=True)
    except OSError as exc:
        raise ValueError("EXIT_ROLLBACK_INPUT_CURRENT_MISSING") from exc
    if release.parent != _RUNTIME_RELEASES:
        _deny("BASELINE_RELEASE_PATH_UNTRUSTED")
    # systemd unit identity must be read independently by root management:
    # the runtime user cannot traverse /etc/systemd/system (0700).
    return _audit_legacy_exit_input(
        release=release,
        unit=None,
        expected_sha=release.name,
        release_owner_uid=uid,
        unit_owner_uid=None,
    )
