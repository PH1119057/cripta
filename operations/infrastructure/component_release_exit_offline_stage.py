"""Bounded OFFLINE source-tree materialization of a reviewed Exit candidate.

#849 Phase 2g. This is NOT a production installer or component service.
Output is confined to disposable research/test sandboxes; its parent remains
runtime-actor-writable, and NO installed/loaded release marker is created.
No syscalls affect systemd, PostgreSQL, Bybit, Gateway, ARM or /srv/cripta.
Only a future separately reviewed root no-argument owner can install
components into the protected /srv/cripta/components tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY = "operations/connectivity/universal_exit_consumer.py"
_LOCK = "operations/infrastructure/exit_component_requirements.lock"
_SIBLINGS = (
    "operations/connectivity/p90_real_independent_exit.py",
    "operations/connectivity/p90_real_persistent_trail.py",
    "operations/connectivity/p90_r06_exit_owner.py",
    "operations/connectivity/p90l90_real_postfill_source_client.py",
)
_REQUIRED = frozenset((_ENTRY, _LOCK, *_SIBLINGS))
_MANIFEST = "component_manifest.json"
_OFFLINE_MARKER = "OFFLINE_TREE_SOURCE_COMMIT"
_MAX_FILES = 5000
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_TREE_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def _deny(reason: str) -> None:
    raise ValueError("EXIT_OFFLINE_STAGE_" + reason)


def _offline_parent(parent: Path) -> None:
    if not isinstance(parent, Path) or not parent.is_absolute():
        _deny("PARENT_NOT_ABSOLUTE")
    if not parent.is_dir() or parent.is_symlink():
        _deny("PARENT_MISSING_OR_SYMLINK")
    # On the real Linux host, refuse *all* production filesystem roots;
    # offline tests use /tmp or the approved Data SSD research contour.
    if os.name == "posix" and not (
        parent.is_relative_to("/tmp")
        or parent.is_relative_to("/data/cripta/research")
    ):
        _deny("PARENT_OUTSIDE_OFFLINE_CONTOUR")
    cursor = parent
    while cursor != cursor.parent:
        if cursor.is_symlink():
            _deny("PARENT_ANCESTOR_SYMLINK")
        cursor = cursor.parent
    if not os.access(parent, os.W_OK | os.X_OK):
        _deny("PARENT_ACTOR_NOT_WRITABLE")
    if os.name == "posix" and parent.stat().st_uid != os.geteuid():
        _deny("PARENT_ACTOR_NOT_OWNER")


def _path_ok(name: str) -> bool:
    if (
        not isinstance(name, str) or not name
        or name.startswith("/") or "\\" in name or ":" in name
        or "\x00" in name or name.endswith("/")
        or any(bit in ("", ".", "..") for bit in name.split("/"))
    ):
        return False
    if name in (_OFFLINE_MARKER, _MANIFEST):
        return False
    return (
        name.startswith("src/")
        or name in _REQUIRED
    )


def _read_reviewed_archive(
    bundle_zip: Path, sha: str,
) -> tuple[dict[str, bytes], dict[str, int], str]:
    if (
        not isinstance(bundle_zip, Path)
        or not bundle_zip.is_file() or bundle_zip.is_symlink()
        or bundle_zip.stat().st_size > _MAX_ARCHIVE_BYTES
    ):
        _deny("ARCHIVE_MISSING_OR_UNTRUSTED")
    try:
        with zipfile.ZipFile(bundle_zip) as archive:
            infos = archive.infolist()
            names = [entry.filename for entry in infos]
            if (
                len(names) > _MAX_FILES + 1
                or len(names) != len(set(names))
                or names.count(_MANIFEST) != 1
            ):
                _deny("ARCHIVE_NAMES_INVALID")
            named = dict(zip(names, infos, strict=True))
            if not all(name == _MANIFEST or _path_ok(name) for name in names):
                _deny("ARCHIVE_PATH_INVALID")
            if named[_MANIFEST].file_size > 1_000_000:
                _deny("MANIFEST_OVERSIZE")
            manifest_raw = archive.read(_MANIFEST)
            manifest = json.loads(manifest_raw.decode("utf-8"))
            if not isinstance(manifest, dict):
                _deny("MANIFEST_INVALID")
            file_hashes = manifest.get("blob_sha256")
            if not isinstance(file_hashes, dict):
                _deny("MANIFEST_FILES_INVALID")
            if (
                type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] != 1
                or manifest.get("component") != "EXIT"
                or manifest.get("target") != sha
                or manifest.get("status") != "STAGED_ONLY_NOT_DEPLOYABLE"
                or manifest.get("install_authorized") is not False
                or manifest.get("entrypoint") != _ENTRY
                or manifest.get("dependency_lock") != _LOCK
                or manifest.get("dynamic_import_siblings") != list(_SIBLINGS)
                or not _DIGEST.fullmatch(str(manifest.get("dependency_lock_sha256")))
            ):
                _deny("MANIFEST_CONTRACT_INVALID")
            if (
                set(file_hashes) != set(names) - {_MANIFEST}
                or not _REQUIRED <= set(file_hashes)
                or not any(name.startswith("src/") for name in file_hashes)
                or manifest["dependency_lock_sha256"] != file_hashes[_LOCK]
                or any(not _DIGEST.fullmatch(str(hash_value))
                       for hash_value in file_hashes.values())
            ):
                _deny("MANIFEST_FILE_CLOSURE_INVALID")
            if sum(entry.file_size for entry in infos) > _MAX_TREE_BYTES:
                _deny("TREE_TOO_LARGE")
            contents: dict[str, bytes] = {}
            modes: dict[str, int] = {}
            for name, expected in file_hashes.items():
                info = named[name]
                permission = info.external_attr >> 16
                if (
                    info.is_dir() or info.file_size > _MAX_FILE_BYTES
                    or stat.S_IFMT(permission) != stat.S_IFREG
                    or stat.S_IMODE(permission) not in (0o644, 0o755)
                ):
                    _deny("FILE_TYPE_OR_MODE_UNTRUSTED")
                raw = archive.read(name)
                if hashlib.sha256(raw).hexdigest() != expected:
                    _deny("FILE_HASH_MISMATCH")
                contents[name] = raw
                modes[name] = permission
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError,
            KeyError, RuntimeError) as exc:
        raise ValueError("EXIT_OFFLINE_STAGE_ARCHIVE_INVALID") from exc
    return contents, modes, hashlib.sha256(manifest_raw).hexdigest()


def stage_exit_bundle_offline(
    *, bundle_zip: Path, sandbox_parent: Path, expected_sha: str,
) -> dict[str, object]:
    """Stage verified source bytes in offline temp only; never install service."""
    if not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha):
        _deny("SOURCE_SHA_INVALID")
    _offline_parent(sandbox_parent)
    output = sandbox_parent / ("exit_" + expected_sha)
    if output.exists() or output.is_symlink():
        _deny("OUTPUT_ALREADY_EXISTS")
    contents, modes, manifest_digest = _read_reviewed_archive(
        bundle_zip, expected_sha,
    )
    scratch: Path | None = None
    try:
        scratch = Path(tempfile.mkdtemp(
            prefix=".exit-offline-", dir=sandbox_parent,
        ))
        for rel, raw in contents.items():
            item = scratch.joinpath(*rel.split("/"))
            item.parent.mkdir(parents=True, exist_ok=True)
            with item.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "posix":
                os.chmod(item, 0o555 if stat.S_IMODE(modes[rel]) == 0o755
                         else 0o444)
        marker = scratch / _OFFLINE_MARKER
        with marker.open("xb") as handle:
            handle.write((expected_sha + "\n").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(marker, 0o444)
        if output.exists() or output.is_symlink():
            _deny("OUTPUT_RACE_DETECTED")
        os.rename(scratch, output)
        scratch = None
    finally:
        if scratch is not None:
            shutil.rmtree(scratch)
    return {
        "status": "OFFLINE_TREE_NOT_DEPLOYABLE",
        "source_commit": expected_sha,
        "staged_tree": str(output),
        "file_count": len(contents),
        "manifest_sha256": manifest_digest,
        "install_authorized": False,
        "immutable_root_attested": False,
        "component_loaded": False,
        "rollback_attested": False,
        "financial_mutation_authorized": False,
    }
