"""#849: fixed root-owned NEW Exit immutable candidate preparation.

This is a real protected filesystem artifact stage, not a service cutover.
The NO-ARGUMENT production owner selects exactly one GitHub-main-derived
archive and one independently pinned Python environment. Untrusted candidate
files can never choose a destination, unit, actor, package or systemd command.

A successful stage is ONLY a source/dependency/unit *physical* readback.
It never implies LIVE authorization, rollback readiness or trading E2E.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import zipfile
from pathlib import Path

from component_release_exit_legacy_prepare import (
    _ISOLATED_VENV_FINGERPRINT,
    _PYTHON_LINKS,
    _VENV,
    _fingerprint_venv,
    _normalize_root_owned_tree,
)
from component_release_exit_owner_preflight import (
    _ORIGINAL_UNIT_SHA256,
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

_COMMIT = "b451165846af286e2f31404d9cdb4eb5cf599031"
_ARCHIVE_SHA256 = "2193d32f24f0f2980a7678e844a93526cf5963f1c0bdaa36d4a74660c42b29d7"
_LOCK_SHA256 = "eb9a7aea12c9be5836b32c2c18570adf395f0512d3c3f1bff10a1853d3ef6c15"
_MANIFEST_SHA256 = "ed8d5a5c95d05583fc245122ebb786508a68deba8ae1550dffd86b4cd86636ea"
_ARCHIVE = Path(
    "/data/cripta/research/testenvs/component_release_849/"
    "p90_exit_bundle_" + _COMMIT + ".zip"
)
_PARENT = Path("/srv/cripta/components/exit/releases")
_UNIT = Path("/etc/systemd/system/cripta-universal-exit-consumer.service")
_MANIFEST = "EXIT_COMPONENT_PREPARED.json"
_UNIT_NAME = "pinned_exit.service"
_ENTRY = "operations/connectivity/universal_exit_consumer.py"
_LOCK = "operations/infrastructure/exit_component_requirements.lock"
_SIBLINGS = (
    "operations/connectivity/p90_real_independent_exit.py",
    "operations/connectivity/p90_real_persistent_trail.py",
    "operations/connectivity/p90_r06_exit_owner.py",
    "operations/connectivity/p90l90_real_postfill_source_client.py",
)
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ARCHIVE = 64 * 1024 * 1024
_MAX_FILES = 5000
_MAX_FILE = 16 * 1024 * 1024
_MAX_TREE = 128 * 1024 * 1024
_MAX_VENV = 180 * 1024 * 1024


def _deny(reason: str) -> None:
    raise ValueError("EXIT_NEW_INSTALL_" + reason)


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _valid_rel(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or "\\" in value or ":" in value or "\x00" in value:
        return False
    if any(p in ("", ".", "..") for p in value.split("/")):
        return False
    return value.startswith("src/") or value in {_ENTRY, _LOCK, *_SIBLINGS}


def _verify_candidate_zip(
    raw: bytes, *, expected_archive_sha256: str,
    expected_commit: str, expected_lock_sha256: str,
) -> tuple[dict[str, bytes], str]:
    """One immutable in-memory archive read; reject all variable paths."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_ARCHIVE:
        _deny("ARCHIVE_SIZE")
    if _hash(raw) != expected_archive_sha256:
        _deny("ARCHIVE_HASH")
    if not _SHA40.fullmatch(expected_commit):
        _deny("COMMIT_INVALID")
    if not _SHA256.fullmatch(expected_lock_sha256):
        _deny("LOCK_DIGEST_INVALID")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [z.filename for z in infos]
            if (
                len(names) > _MAX_FILES + 1
                or len(names) != len(set(names))
                or names.count("component_manifest.json") != 1
                or any(not _valid_rel(n) and n != "component_manifest.json"
                       for n in names)
            ):
                _deny("ZIP_NAMES_INVALID")
            indexed = dict(zip(names, infos, strict=True))
            marker_info = indexed["component_manifest.json"]
            if marker_info.file_size > 1_000_000:
                _deny("MANIFEST_OVERSIZE")
            marker_bytes = archive.read("component_manifest.json")
            meta = json.loads(marker_bytes)
            if (
                not isinstance(meta, dict)
                or meta.get("schema_version") != 1
                or meta.get("component") != "EXIT"
                or meta.get("target") != expected_commit
                or meta.get("status") != "STAGED_ONLY_NOT_DEPLOYABLE"
                or meta.get("install_authorized") is not False
                or meta.get("entrypoint") != _ENTRY
                or meta.get("dependency_lock") != _LOCK
                or meta.get("dynamic_import_siblings") != list(_SIBLINGS)
                or meta.get("dependency_lock_sha256") != expected_lock_sha256
            ):
                _deny("MANIFEST_CONTRACT")
            hashes = meta.get("blob_sha256")
            if (
                not isinstance(hashes, dict)
                or set(hashes) != set(names) - {"component_manifest.json"}
                or not {_ENTRY, _LOCK, *_SIBLINGS} <= set(hashes)
                or not any(n.startswith("src/") for n in hashes)
                or any(not _SHA256.fullmatch(str(v)) for v in hashes.values())
            ):
                _deny("MANIFEST_CLOSURE")
            if sum(z.file_size for z in infos) > _MAX_TREE:
                _deny("TREE_BOUNDS")
            contents: dict[str, bytes] = {}
            for name, digest in hashes.items():
                info = indexed[name]
                mode = info.external_attr >> 16
                if (
                    info.is_dir() or info.file_size > _MAX_FILE
                    or stat.S_IFMT(mode) != stat.S_IFREG
                    or stat.S_IMODE(mode) not in (0o644, 0o755)
                ):
                    _deny("ZIP_FILE_METADATA")
                data = archive.read(name)
                if _hash(data) != digest:
                    _deny("ZIP_BLOB_HASH")
                contents[name] = data
            if _hash(contents[_LOCK]) != expected_lock_sha256:
                _deny("ZIP_LOCK_HASH")
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeDecodeError,
            json.JSONDecodeError, RuntimeError) as exc:
        raise ValueError("EXIT_NEW_INSTALL_ARCHIVE_INVALID") from exc
    return contents, _hash(marker_bytes)


def _write_durable(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _hash_file(path: Path, limit: int) -> str:
    if path.is_symlink() or not path.is_file():
        _deny("READBACK_NONREGULAR_FILE")
    z = path.lstat()
    if not stat.S_ISREG(z.st_mode) or z.st_nlink != 1 or z.st_size > limit:
        _deny("READBACK_FILE_METADATA")
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(part)
            if size > limit:
                _deny("READBACK_FILE_OVERSIZE")
            h.update(part)
    if size != z.st_size:
        _deny("READBACK_FILE_CHANGED")
    return h.hexdigest()


def _tree_digest(root: Path, *, owner_uid: int, service_gid: int) -> str:
    """Exact owner/mode/symlink/content map; no mutable runtime imports."""
    records: list[tuple[str, str, int, int, str]] = []
    regular_bytes = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            f = Path(base) / name
            rel = f.relative_to(root).as_posix()
            if rel == _MANIFEST:
                continue
            if not _valid_rel(rel) and not (
                rel == _UNIT_NAME or rel == ".venv"
                or rel.startswith(".venv/")
                or any(item.startswith(rel + "/") for item in
                       (_ENTRY, _LOCK, *_SIBLINGS, "src/bybit_workbench"))
            ):
                _deny("TREE_PATH_UNEXPECTED")
            z = f.lstat()
            mode = stat.S_IMODE(z.st_mode)
            if (z.st_uid, z.st_gid) != (owner_uid, service_gid):
                _deny("TREE_OWNER_DRIFT")
            if stat.S_ISDIR(z.st_mode):
                if mode != 0o750:
                    _deny("TREE_DIR_MODE")
                kind, digest = "D", ""
            elif stat.S_ISLNK(z.st_mode):
                if (
                    not rel.startswith(".venv/")
                    or _PYTHON_LINKS.get(rel.removeprefix(".venv/"))
                    != os.readlink(f)
                ):
                    _deny("TREE_SYMLINK")
                kind, digest = "L", os.readlink(f)
            elif stat.S_ISREG(z.st_mode):
                if mode not in (0o640, 0o750) or z.st_nlink != 1:
                    _deny("TREE_FILE_MODE_OR_LINK")
                limit = _MAX_FILE if not rel.startswith(".venv/") else 32 * 1024 * 1024
                digest = _hash_file(f, limit)
                regular_bytes += z.st_size
                if regular_bytes > _MAX_TREE + _MAX_VENV:
                    _deny("TREE_SIZE")
                kind = "F"
            else:
                _deny("TREE_SPECIAL")
            records.append((rel, kind, mode, 0 if kind == "D" else z.st_size, digest))
            if len(records) > _MAX_FILES * 2:
                _deny("TREE_ENTRY_COUNT")
    h = hashlib.sha256()
    for rel, kind, mode, size, digest in sorted(records):
        h.update(f"{kind}\0{rel}\0{mode:o}\0{size}\0{digest}\n".encode())
    return h.hexdigest()


def _readback_prepared_candidate(
    *, candidate_root: Path, owner_uid: int, service_gid: int,
    expected_commit: str, expected_archive_sha256: str,
) -> dict[str, object]:
    if (
        not candidate_root.is_dir() or candidate_root.is_symlink()
        or candidate_root.name != expected_commit
    ):
        _deny("READBACK_CANDIDATE_ROOT")
    z = candidate_root.lstat()
    if (
        (z.st_uid, z.st_gid, stat.S_IMODE(z.st_mode))
        != (owner_uid, service_gid, 0o750)
    ):
        _deny("READBACK_ROOT_OWNER_MODE")
    marker = candidate_root / _MANIFEST
    if marker.is_symlink() or not marker.is_file():
        _deny("READBACK_MARKER_MISSING")
    mz = marker.lstat()
    if (
        (mz.st_uid, mz.st_gid, stat.S_IMODE(mz.st_mode))
        != (owner_uid, service_gid, 0o640)
    ):
        _deny("READBACK_MARKER_OWNER")
    try:
        data = json.loads(marker.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("EXIT_NEW_INSTALL_READBACK_MARKER_INVALID") from exc
    if (
        not isinstance(data, dict)
        or data.get("state") != "IMMUTABLE_EXIT_CANDIDATE_PREPARED"
        or data.get("component_id") != "EXIT"
        or data.get("source_commit") != expected_commit
        or data.get("archive_sha256") != expected_archive_sha256
        or data.get("unit_sha256") != _hash_file(candidate_root / _UNIT_NAME, 16 * 1024)
        or data.get("rollback_ready") is not False
        or data.get("cutover_authorized") is not False
        or data.get("new_component_loaded") is not False
        or data.get("financial_mutation_authorized") is not False
    ):
        _deny("READBACK_MARKER_CONTRACT")
    hashes = data.get("blob_sha256")
    if (
        not isinstance(hashes, dict)
        or len(hashes) > _MAX_FILES
        or not {_ENTRY, _LOCK, *_SIBLINGS} <= set(hashes)
    ):
        _deny("READBACK_BLOBS_INVALID")
    for rel, digest in hashes.items():
        if not _valid_rel(rel) or not _SHA256.fullmatch(str(digest)):
            _deny("READBACK_BLOB_PATH")
        if _hash_file(candidate_root / rel, _MAX_FILE) != digest:
            _deny("READBACK_BLOB_HASH")
    payload = _tree_digest(
        candidate_root, owner_uid=owner_uid, service_gid=service_gid,
    )
    if payload != data.get("payload_sha256"):
        _deny("READBACK_TREE_HASH")
    return {
        "state": "IMMUTABLE_EXIT_CANDIDATE_PREPARED",
        "component_id": "EXIT",
        "source_commit": expected_commit,
        "candidate_root": str(candidate_root),
        "archive_sha256": expected_archive_sha256,
        "unit_sha256": data["unit_sha256"],
        "payload_sha256": payload,
        "file_count": len(hashes),
        "physical_file_hashes_verified": True,
        "new_component_loaded": False,
        "cutover_authorized": False,
        "rollback_ready": False,
        "runtime_behavior_verified": False,
        "financial_mutation_authorized": False,
        "systemd_mutation_performed": False,
    }


def _stage_prepared_candidate(
    *, component_parent: Path, commit: str,
    contents: dict[str, bytes], manifest_sha256: str,
    archive_sha256: str, isolated_venv: Path,
    expected_venv_fingerprint: str, unit_bytes: bytes,
    owner_uid: int, service_gid: int,
) -> dict[str, object]:
    """File-IO fixture shared by strict fixed-path production owner, not a grant."""
    if not _SHA40.fullmatch(commit):
        _deny("COMMIT_INVALID")
    if (
        component_parent.is_symlink() or not component_parent.is_dir()
        or not isinstance(contents, dict) or not contents
        or any(not _valid_rel(rel) or not isinstance(value, bytes)
               for rel, value in contents.items())
        or not _SHA256.fullmatch(manifest_sha256)
        or not _SHA256.fullmatch(archive_sha256)
        or not _SHA256.fullmatch(expected_venv_fingerprint)
        or not isinstance(unit_bytes, bytes) or not 0 < len(unit_bytes) <= 16 * 1024
    ):
        _deny("INPUT_INVALID")
    p = component_parent.lstat()
    if (p.st_uid, p.st_gid, stat.S_IMODE(p.st_mode)) != (
        owner_uid, service_gid, 0o750
    ):
        _deny("PARENT_OWNER_MODE")
    candidate = component_parent / commit
    stage = component_parent / (".stage-" + commit)
    if (
        candidate.exists() or candidate.is_symlink()
        or stage.exists() or stage.is_symlink()
    ):
        _deny("ALREADY_EXISTS")
    before = _fingerprint_venv(isolated_venv)
    if before["fingerprint"] != expected_venv_fingerprint:
        _deny("VENV_FINGERPRINT_CHANGED")
    # Input checks complete. No deletion/overwrite on interrupted stage.
    stage.mkdir(mode=0o700)
    for rel, payload in sorted(contents.items()):
        path = stage.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_durable(path, payload)
    _write_durable(stage / _UNIT_NAME, unit_bytes)
    shutil.copytree(isolated_venv, stage / ".venv", symlinks=True)
    copied = _fingerprint_venv(stage / ".venv")
    after = _fingerprint_venv(isolated_venv)
    if copied != before or after != before:
        _deny("VENV_CHANGED_DURING_COPY")
    _normalize_root_owned_tree(stage, owner_uid, service_gid)
    payload_hash = _tree_digest(
        stage, owner_uid=owner_uid, service_gid=service_gid,
    )
    marker = {
        "state": "IMMUTABLE_EXIT_CANDIDATE_PREPARED",
        "component_id": "EXIT",
        "source_commit": commit,
        "archive_sha256": archive_sha256,
        "manifest_sha256": manifest_sha256,
        "venv_input_fingerprint": expected_venv_fingerprint,
        "blob_sha256": {rel: _hash(data) for rel, data in sorted(contents.items())},
        "unit_sha256": _hash(unit_bytes),
        "payload_sha256": payload_hash,
        "new_component_loaded": False,
        "cutover_authorized": False,
        "rollback_ready": False,
        "runtime_behavior_verified": False,
        "financial_mutation_authorized": False,
    }
    marker_path = stage / _MANIFEST
    _write_durable(
        marker_path, json.dumps(marker, sort_keys=True,
                                separators=(",", ":")).encode("utf-8") + b"\n",
    )
    os.chown(marker_path, owner_uid, service_gid, follow_symlinks=False)
    os.chmod(marker_path, 0o640)
    fd = os.open(stage, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(stage, candidate)
    fd = os.open(component_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return _readback_prepared_candidate(
        candidate_root=candidate, owner_uid=owner_uid, service_gid=service_gid,
        expected_commit=commit, expected_archive_sha256=archive_sha256,
    )


def _read_fixed_input(path: Path, *, uid: int, gid: int, mode: int, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        z = os.fstat(fd)
        if (
            not stat.S_ISREG(z.st_mode) or z.st_nlink != 1
            or (z.st_uid, z.st_gid, stat.S_IMODE(z.st_mode)) != (uid, gid, mode)
            or not 0 < z.st_size <= limit
        ):
            _deny("INPUT_FILE_OWNER_MODE")
        with os.fdopen(os.dup(fd), "rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) != z.st_size:
            _deny("INPUT_FILE_CHANGED")
        return raw
    finally:
        os.close(fd)


def prepare_exit_new_component_once() -> dict[str, object]:
    """Root-only NO-ARGUMENT *new payload stage*; no daemon/service actions."""
    if sys.platform != "linux":
        _deny("LINUX_ONLY")
    if os.geteuid() != 0:
        _deny("ROOT_REQUIRED")
    import grp
    import pwd

    group = grp.getgrnam("cripta").gr_gid
    actor = pwd.getpwnam("cripta").pw_uid
    with _hold_shared_release_lock(path=_SHARED_LOCK, uid=0, gid=0):
        if inspect_exit_root_tree()["state"] != "ROOT_TREE_PREPARED_METADATA_ONLY":
            _deny("ROOT_TREE_UNTRUSTED")
        old = attest_old_exit_candidate_on_host()
        if (
            old.get("state") != "OLD_EXIT_RECOVERY_STAGED_READBACK_PASS"
            or old.get("actual_host_attested") is not True
            or old.get("rollback_ready") is not False
            or old.get("install_authorized") is not False
        ):
            _deny("OLD_PROTECTED_CANDIDATE_NOT_READBACK_PROVEN")
        subs = _sub_private_units()
        before_units = _snapshot_units(subs)
        if before_units != _snapshot_units(subs):
            _deny("LIVE_UNIT_BASELINE_NOT_STABLE")
        if _installed_unit_sha256() != _ORIGINAL_UNIT_SHA256:
            _deny("INSTALLED_LEGACY_UNIT_CHANGED")
        archive = _read_fixed_input(
            _ARCHIVE, uid=actor, gid=group, mode=0o444, limit=_MAX_ARCHIVE,
        )
        contents, marker_sha256 = _verify_candidate_zip(
            archive, expected_archive_sha256=_ARCHIVE_SHA256,
            expected_commit=_COMMIT, expected_lock_sha256=_LOCK_SHA256,
        )
        if len(contents) != 437 or marker_sha256 != _MANIFEST_SHA256:
            _deny("REVIEWED_BLOB_INVENTORY_CHANGED")
        unit_source = _read_fixed_input(
            _UNIT, uid=0, gid=0, mode=0o644, limit=16 * 1024,
        )
        if _hash(unit_source) != _ORIGINAL_UNIT_SHA256:
            _deny("ORIGINAL_EXIT_UNIT_BYTES_CHANGED")
        proposal = build_exit_unit_candidate(
            source_unit=unit_source.decode("utf-8"),
            component_commit=_COMMIT,
            component_lock_sha256=_LOCK_SHA256,
        )
        if (
            proposal.get("status") != "STAGED_ONLY_NOT_DEPLOYABLE"
            or proposal.get("install_authorized") is not False
            or proposal.get("component_release_root") != str(_PARENT / _COMMIT)
        ):
            _deny("PINNED_UNIT_CONTRACT_CHANGED")
        vstat = _VENV.lstat()
        if (
            not stat.S_ISDIR(vstat.st_mode)
            or vstat.st_uid != actor or _VENV.is_symlink()
        ):
            _deny("ISOLATED_VENV_ACTOR")
        result = _stage_prepared_candidate(
            component_parent=_PARENT, commit=_COMMIT,
            contents=contents, manifest_sha256=marker_sha256,
            archive_sha256=_ARCHIVE_SHA256, isolated_venv=_VENV,
            expected_venv_fingerprint=_ISOLATED_VENV_FINGERPRINT,
            unit_bytes=proposal["unit_text"].encode("utf-8"),
            owner_uid=0, service_gid=group,
        )
        # An unrelated runtime drift is never labeled stage acceptance.
        if before_units != _snapshot_units(subs):
            _deny("LIVE_NEIGHBOR_CHANGED_DURING_NEW_STAGE")
        if _installed_unit_sha256() != _ORIGINAL_UNIT_SHA256:
            _deny("ORIGINAL_EXIT_UNIT_CHANGED_DURING_NEW_STAGE")
        # A real on-host protected stage is NOT a live release approval.
        return {**result, "root_actor_verified": True,
                "shared_release_lock_checked": True,
                "neighbor_pid_restart_snapshot_stable": True,
                "old_rollback_artifact_readback": "PASS_STAGED_ONLY"}


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("EXIT_NEW_INSTALL_NO_ARGUMENTS")
    try:
        print(json.dumps(prepare_exit_new_component_once(), sort_keys=True))
    except (OSError, ValueError) as exc:
        print("EXIT_NEW_INSTALL_BLOCKED:" + type(exc).__name__)
        raise SystemExit(1) from None
