"""#849: fixed, fail-closed OLD Exit rollback candidate preparation (NO deploy).

The only production entrypoint takes NO arguments. It copies already protected
legacy INPUT bytes and an exact, previously hash-attested isolated Python
dependency closure into a root-owned rollback directory. It DOES NOT install
the unit, touch a service, write installed/loaded markers, or authorize a trade.

The alternate-path fixture is private/test-only. Before any privileged use,
the exact GitHub source blob, lock, actor, inputs and parent ACL must also be
approved and verified through the designated management rail.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tarfile
from pathlib import Path, PurePosixPath

from component_release_exit_root_bootstrap import (
    _hold_shared_release_lock,
    inspect_exit_root_tree,
)

OLD_EXIT_COMMIT = "8bd45e9874d6ce811d7261ee152fc2c43729e1b9"
_SOURCE_ARCHIVE_SHA = "f80c2329b7dec31dab276c3bbab12a41e4e5ed3c02e293915d8f5f0f416a029e"
_OLD_UNIT_SHA = "c4a21004e637df794670d7d096c6ffa443c23a881c662b65bebfc11459433248"
_ISOLATED_VENV_FINGERPRINT = "c539b994dc63689ea3b08b614d5347a69148cef9c5dcac5c33894b6464fe7c9b"
_ROOT = Path("/srv/cripta/components/exit/rollback")
_INPUT = _ROOT / (".input-" + OLD_EXIT_COMMIT)
_VENV = Path("/data/cripta/research/testenvs/component_release_849/exit_exact_component_lock_d40f/.venv")
_LOCK = Path("/run/lock/cripta-install-verified-release.lock")
_MANIFEST = "OLD_EXIT_ROLLBACK_CANDIDATE.json"
_UNIT = "legacy_exit_pinned.service"
_PYTHON_LINKS = {
    "bin/python": "python3",
    "bin/python3": "/usr/bin/python3",
    "bin/python3.12": "python3",
    "lib64": "lib",
}
_MAX_MEMBERS = 5000
_MAX_ARCHIVE_SIZE = 64 * 1024 * 1024
_MAX_INPUT_BYTES = 128 * 1024 * 1024
_MAX_VENV_BYTES = 180 * 1024 * 1024


def _deny(code: str) -> None:
    raise ValueError("EXIT_OLD_ROLLBACK_" + code)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _file_hash(path: Path, max_bytes: int) -> str:
    if path.is_symlink():
        _deny("INPUT_SYMLINK")
    try:
        z = path.lstat()
        if not stat.S_ISREG(z.st_mode) or z.st_size > max_bytes:
            _deny("INPUT_FILE_TYPE_OR_SIZE")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as fh:
            opened = os.fstat(fh.fileno())
            if (opened.st_ino, opened.st_dev, opened.st_size) != (
                z.st_ino, z.st_dev, z.st_size
            ):
                _deny("INPUT_CHANGED")
            result = hashlib.file_digest(fh, "sha256").hexdigest()
            after = os.fstat(fh.fileno())
            if after.st_size != z.st_size or after.st_mtime_ns != z.st_mtime_ns:
                _deny("INPUT_CHANGED")
            return result
    except OSError as exc:
        raise ValueError("EXIT_OLD_ROLLBACK_INPUT_READ") from exc


def _fingerprint_venv(root: Path) -> dict[str, object]:
    """Bounded byte+mode fingerprint; allowed Python interpreter links only."""
    if not isinstance(root, Path) or not root.is_absolute() or root.is_symlink():
        _deny("VENV_ROOT")
    if not root.is_dir():
        _deny("VENV_MISSING")
    h = hashlib.sha256()
    links: dict[str, str] = {}
    count = 0
    nbytes = 0
    members = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            z = path.lstat()
            if stat.S_ISDIR(z.st_mode):
                kind, digest = "D", ""
            elif stat.S_ISLNK(z.st_mode):
                kind, digest = "L", os.readlink(path)
                if _PYTHON_LINKS.get(rel) != digest:
                    _deny("VENV_SYMLINK")
                links[rel] = digest
            elif stat.S_ISREG(z.st_mode):
                kind = "F"
                digest = _file_hash(path, 32 * 1024 * 1024)
                count += 1
                nbytes += z.st_size
            else:
                _deny("VENV_SPECIAL_FILE")
            members += 1
            if members > _MAX_MEMBERS or nbytes > _MAX_VENV_BYTES:
                _deny("VENV_BOUNDS")
            h.update(
                f"{kind}\0{rel}\0{stat.S_IMODE(z.st_mode):o}\0"
                f"{z.st_size}\0{digest}\n".encode("utf-8")
            )
    if links != _PYTHON_LINKS or count < 2:
        _deny("VENV_LINKS_OR_PACKAGES")
    config = root / "pyvenv.cfg"
    if config.is_symlink() or not config.is_file():
        _deny("VENV_CONFIG")
    lines = config.read_text(encoding="utf-8").splitlines()
    site_flags = [
        line.partition("=")[2].strip().lower() for line in lines
        if line.partition("=")[0].strip() == "include-system-site-packages"
    ]
    if site_flags != ["false"]:
        _deny("VENV_SYSTEM_SITE_PACKAGES")
    return {
        "fingerprint": h.hexdigest(),
        "regular_file_count": count,
        "regular_file_bytes": nbytes,
        "symlink_count": len(links),
    }


def build_pinned_old_exit_unit(source_unit: bytes) -> str:
    """Exact reviewed old unit; all runtime paths point at protected OLD root."""
    if not isinstance(source_unit, bytes) or hashlib.sha256(
        source_unit
    ).hexdigest() != _OLD_UNIT_SHA:
        _deny("UNIT_UNAPPROVED")
    old = "/srv/cripta/runtime/current"
    root = str(_ROOT / OLD_EXIT_COMMIT)
    text = source_unit.decode("utf-8")
    replacements = {
        "WorkingDirectory=" + old: "WorkingDirectory=" + root,
        "ExecStart=" + old + "/.venv/bin/python " + old
        + "/operations/connectivity/universal_exit_consumer.py": (
            "ExecStart=" + root + "/.venv/bin/python " + root
            + "/operations/connectivity/universal_exit_consumer.py"
        ),
        "Environment=PYTHONPATH=" + old + "/src:" + old
        + "/.venv/lib/python3.12/site-packages": (
            "Environment=PYTHONPATH=" + root + "/src:"
            + root + "/operations/connectivity"
        ),
        "EnvironmentFile=-/etc/cripta/release.env": (
            "Environment=CRIPTA_RELEASE_COMMIT=" + OLD_EXIT_COMMIT
            + "\nEnvironment=CRIPTA_RUNTIME_ROOT=" + root
        ),
    }
    lines = text.splitlines()
    for original, updated in replacements.items():
        if lines.count(original) != 1:
            _deny("UNIT_REQUIRED_LINE_MISSING")
        lines[lines.index(original)] = updated
    prepared = "\n".join(lines) + "\n"
    for required in (
        "User=cripta", "Group=cripta", "ProtectSystem=strict",
        "NoNewPrivileges=true", "PrivateTmp=true", "ProtectHome=true",
        "EnvironmentFile=-/etc/cripta/universal_exit_consumer.env",
        "StateDirectory=cripta-p90-r06-exit",
    ):
        if prepared.splitlines().count(required) != 1:
            _deny("UNIT_REQUIRED_SECURITY_LINE_MISSING")
    if old in prepared or "EnvironmentFile=-/etc/cripta/release.env" in prepared:
        _deny("UNIT_LEGACY_RUNTIME_REMAINING")
    return prepared


def _validated_tar_members(archive: Path, expected_sha256: str) -> list[
    tuple[str, bytes | None, bool]
]:
    if not _is_sha256(expected_sha256) or _file_hash(
        archive, _MAX_ARCHIVE_SIZE
    ) != expected_sha256:
        _deny("ARCHIVE_HASH")
    result: list[tuple[str, bytes | None, bool]] = []
    seen: set[str] = set()
    byte_count = 0
    marker: bytes | None = None
    try:
        with tarfile.open(archive, "r:") as tf:
            for item in tf:
                name = item.name
                pp = PurePosixPath(name)
                if (
                    pp.is_absolute() or ".." in pp.parts
                    or name != pp.as_posix() or name in seen
                ):
                    _deny("ARCHIVE_PATH")
                seen.add(name)
                in_venv = name == ".venv" or name.startswith(".venv/")
                in_source = (
                    name in ("src", "operations", "operations/connectivity",
                             "INSTALLED_COMMIT")
                    or name.startswith("src/")
                    or name.startswith("operations/connectivity/")
                )
                if not (in_venv or in_source):
                    _deny("ARCHIVE_SCOPE")
                if item.isdir():
                    if not in_venv:
                        result.append((name, None, True))
                elif item.isfile():
                    if item.size > 16 * 1024 * 1024:
                        _deny("ARCHIVE_OVERSIZE")
                    byte_count += item.size
                    if byte_count > _MAX_INPUT_BYTES:
                        _deny("ARCHIVE_BOUNDS")
                    if not in_venv:
                        stream = tf.extractfile(item)
                        if stream is None:
                            _deny("ARCHIVE_UNREADABLE_MEMBER")
                        data = stream.read(item.size + 1)
                        if len(data) != item.size:
                            _deny("ARCHIVE_TRUNCATED")
                        result.append((name, data, False))
                        if name == "INSTALLED_COMMIT":
                            marker = data
                elif item.issym() and in_venv and _PYTHON_LINKS.get(
                    name.removeprefix(".venv/")
                ) == item.linkname:
                    pass  # deliberately REPLACED by isolated venv
                else:
                    _deny("ARCHIVE_UNSAFE_MEMBER")
                if len(seen) > _MAX_MEMBERS:
                    _deny("ARCHIVE_TOO_MANY_MEMBERS")
    except (OSError, tarfile.TarError) as exc:
        raise ValueError("EXIT_OLD_ROLLBACK_ARCHIVE_UNREADABLE") from exc
    if marker != (OLD_EXIT_COMMIT + "\n").encode("ascii"):
        _deny("ARCHIVE_MARKER_MISMATCH")
    if not any(n == "src" or n.startswith("src/") for n, _, _ in result):
        _deny("ARCHIVE_SOURCE_MISSING")
    if not any(n == "operations/connectivity/universal_exit_consumer.py"
               for n, _, _ in result):
        _deny("ARCHIVE_ENTRYPOINT_MISSING")
    return result


def _normalize_root_owned_tree(root: Path, uid: int, gid: int) -> None:
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            z = path.lstat()
            if stat.S_ISLNK(z.st_mode):
                # All copied symlinks were already validated by venv scanner.
                os.lchown(path, uid, gid)
                continue
            if not (stat.S_ISDIR(z.st_mode) or stat.S_ISREG(z.st_mode)):
                _deny("STAGE_SPECIAL_FILE")
            os.chown(path, uid, gid, follow_symlinks=False)
            os.chmod(
                path, 0o750 if stat.S_ISDIR(z.st_mode)
                or (stat.S_IMODE(z.st_mode) & 0o111) else 0o640,
                follow_symlinks=False,
            )
    os.chown(root, uid, gid, follow_symlinks=False)
    os.chmod(root, 0o750)


def _payload_digest(root: Path, owner_uid: int, group_gid: int) -> str:
    h = hashlib.sha256()
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            if rel == _MANIFEST:
                continue
            z = path.lstat()
            mode = stat.S_IMODE(z.st_mode)
            if z.st_uid != owner_uid or z.st_gid != group_gid:
                _deny("READBACK_OWNER")
            if stat.S_ISDIR(z.st_mode):
                if mode != 0o750:
                    _deny("READBACK_DIR_MODE")
                kind, digest = "D", ""
            elif stat.S_ISLNK(z.st_mode):
                if not rel.startswith(".venv/") or _PYTHON_LINKS.get(
                    rel.removeprefix(".venv/")
                ) != os.readlink(path):
                    _deny("READBACK_SYMLINK")
                kind, digest = "L", os.readlink(path)
            elif stat.S_ISREG(z.st_mode):
                if mode not in (0o640, 0o750):
                    _deny("READBACK_FILE_MODE")
                kind, digest = "F", _file_hash(path, 64 * 1024 * 1024)
            else:
                _deny("READBACK_SPECIAL_FILE")
            h.update(f"{kind}\0{rel}\0{mode:o}\0{z.st_size}\0{digest}\n".encode())
    return h.hexdigest()


def _verify_legacy_fixture(
    *,
    candidate_root: Path,
    expected_archive_sha256: str,
    expected_venv_fingerprint: str,
    owner_uid: int,
    service_gid: int,
) -> dict[str, object]:
    if (
        not isinstance(candidate_root, Path) or not candidate_root.is_absolute()
        or candidate_root.is_symlink() or candidate_root.name != OLD_EXIT_COMMIT
        or not candidate_root.is_dir()
    ):
        _deny("READBACK_ROOT")
    z = candidate_root.lstat()
    if (
        z.st_uid != owner_uid or z.st_gid != service_gid
        or stat.S_IMODE(z.st_mode) != 0o750
    ):
        _deny("READBACK_ROOT_METADATA")
    marker = candidate_root / _MANIFEST
    if marker.is_symlink() or not marker.is_file():
        _deny("READBACK_MANIFEST")
    if (
        marker.lstat().st_uid != owner_uid
        or marker.lstat().st_gid != service_gid
        or stat.S_IMODE(marker.lstat().st_mode) != 0o640
    ):
        _deny("READBACK_MANIFEST_METADATA")
    try:
        report = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("EXIT_OLD_ROLLBACK_READBACK_MANIFEST_INVALID") from exc
    if (
        report.get("source_commit") != OLD_EXIT_COMMIT
        or report.get("state") != "PROTECTED_OLD_EXIT_CANDIDATE_STAGED"
        or report.get("input_archive_sha256") != expected_archive_sha256
        or report.get("isolated_venv_input_fingerprint") != expected_venv_fingerprint
        or report.get("candidate_root") != str(candidate_root)
        or report.get("rollback_ready") is not False
        or report.get("systemd_unit_installed") is not False
        or report.get("financial_mutation_authorized") is not False
        or report.get("runtime_behavior_verified") is not False
        or report.get("component_installed") is not False
    ):
        _deny("READBACK_MANIFEST_CONTRACT")
    pinned = candidate_root / _UNIT
    if _file_hash(pinned, 16 * 1024) != report.get("pinned_unit_sha256"):
        _deny("READBACK_UNIT")
    if _payload_digest(candidate_root, owner_uid, service_gid) != report.get(
        "candidate_payload_sha256"
    ):
        _deny("READBACK_PAYLOAD")
    if (candidate_root / "COMPONENT_INSTALLED_COMMIT").exists() or (
        candidate_root / "COMPONENT_LOADED_COMMIT"
    ).exists():
        _deny("READBACK_UNAUTHORIZED_IDENTITY")
    return {
        "state": "PROTECTED_OLD_EXIT_CANDIDATE_READBACK_PASS",
        "source_commit": OLD_EXIT_COMMIT,
        "candidate_root": str(candidate_root),
        "candidate_payload_sha256": report["candidate_payload_sha256"],
        "rollback_ready": False,
        "systemd_unit_installed": False,
        "runtime_behavior_verified": False,
        "financial_mutation_authorized": False,
    }


def _prepare_legacy_fixture(
    *,
    archive: Path,
    source_unit: Path,
    isolated_venv: Path,
    rollback_parent: Path,
    lock_path: Path,
    expected_archive_sha256: str,
    expected_venv_fingerprint: str,
    owner_uid: int,
    service_gid: int,
) -> dict[str, object]:
    """Test-path adapter; still enforces exclusive shared lock and no overwrites."""
    if not _is_sha256(expected_archive_sha256) or not _is_sha256(
        expected_venv_fingerprint
    ):
        _deny("EXPECTED_HASH_INVALID")
    if rollback_parent.is_symlink() or not rollback_parent.is_dir():
        _deny("PARENT")
    p = rollback_parent.lstat()
    if (p.st_uid, p.st_gid, stat.S_IMODE(p.st_mode)) != (
        owner_uid, service_gid, 0o750
    ):
        _deny("PARENT_METADATA")
    with _hold_shared_release_lock(path=lock_path, uid=owner_uid, gid=service_gid):
        candidate = rollback_parent / OLD_EXIT_COMMIT
        staging = rollback_parent / (".stage-" + OLD_EXIT_COMMIT)
        if candidate.exists() or candidate.is_symlink() or staging.exists() or staging.is_symlink():
            _deny("CANDIDATE_OR_STAGE_EXISTS")
        venv_before = _fingerprint_venv(isolated_venv)
        if venv_before["fingerprint"] != expected_venv_fingerprint:
            _deny("VENV_HASH")
        unit = build_pinned_old_exit_unit(source_unit.read_bytes())
        members = _validated_tar_members(archive, expected_archive_sha256)
        # No target object exists until all inputs have passed validation.
        staging.mkdir(mode=0o700)
        try:
            for rel, contents, directory in members:
                target = staging.joinpath(*PurePosixPath(rel).parts)
                if directory:
                    target.mkdir(mode=0o750, parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                    with target.open("xb") as handle:
                        assert contents is not None
                        handle.write(contents)
            shutil.copytree(isolated_venv, staging / ".venv", symlinks=True)
            venv_copy = _fingerprint_venv(staging / ".venv")
            venv_after = _fingerprint_venv(isolated_venv)
            if (
                venv_before["fingerprint"] != venv_copy["fingerprint"]
                or venv_before["fingerprint"] != venv_after["fingerprint"]
            ):
                _deny("VENV_CHANGED_DURING_COPY")
            (staging / _UNIT).write_text(unit, encoding="utf-8")
            _normalize_root_owned_tree(staging, owner_uid, service_gid)
            report = {
                "state": "PROTECTED_OLD_EXIT_CANDIDATE_STAGED",
                "source_commit": OLD_EXIT_COMMIT,
                "candidate_root": str(candidate),
                "input_archive_sha256": expected_archive_sha256,
                "source_unit_sha256": _OLD_UNIT_SHA,
                "isolated_venv_input_fingerprint": expected_venv_fingerprint,
                "pinned_unit_sha256": hashlib.sha256(unit.encode()).hexdigest(),
                "candidate_payload_sha256": _payload_digest(
                    staging, owner_uid, service_gid,
                ),
                "rollback_ready": False,
                "systemd_unit_installed": False,
                "runtime_behavior_verified": False,
                "financial_mutation_authorized": False,
                "component_installed": False,
                "component_loaded": False,
                "signed_exchange_protection_verified": False,
            }
            manifest = staging / _MANIFEST
            with manifest.open("xb") as handle:
                handle.write((json.dumps(report, sort_keys=True, indent=2) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
            os.chown(manifest, owner_uid, service_gid, follow_symlinks=False)
            os.chmod(manifest, 0o640)
            os.replace(staging, candidate)
            return _verify_legacy_fixture(
                candidate_root=candidate,
                expected_archive_sha256=expected_archive_sha256,
                expected_venv_fingerprint=expected_venv_fingerprint,
                owner_uid=owner_uid,
                service_gid=service_gid,
            ) | {"state": "PROTECTED_OLD_EXIT_CANDIDATE_STAGED"}
        except Exception:
            # If anything failed after mkdir/rename, preserve the partial
            # exact path for human/independent reconciliation, never retry.
            raise


def prepare_legacy_exit_rollback_once() -> dict[str, object]:
    """Exact no-argument root OS preparation, NOT an Exit release or restart."""
    if os.geteuid() != 0:
        _deny("ROOT_REQUIRED")
    if inspect_exit_root_tree()["state"] != "ROOT_TREE_PREPARED_METADATA_ONLY":
        _deny("PRODUCTION_ROOT_NOT_PROTECTED")
    import grp
    import pwd

    group = grp.getgrnam("cripta").gr_gid
    actor = pwd.getpwnam("cripta").pw_uid
    # The input archive/unit are protected root-owned copies. The separately
    # built venv is cripta-owned and MUST match its previously attested SHA
    # exactly; it is data copied only, never code executed by this root owner.
    if _INPUT.is_symlink() or not _INPUT.is_dir():
        _deny("PROTECTED_INPUT_MISSING")
    z = _INPUT.lstat()
    if (z.st_uid, z.st_gid, stat.S_IMODE(z.st_mode)) != (0, group, 0o750):
        _deny("PROTECTED_INPUT_OWNER")
    for name in ("legacy_runtime.tar", "legacy_unit.service", "INPUT_SNAPSHOT.json"):
        f = _INPUT / name
        if f.is_symlink() or not f.is_file():
            _deny("PROTECTED_INPUT_FILE")
        fz = f.lstat()
        if (fz.st_uid, fz.st_gid, stat.S_IMODE(fz.st_mode)) != (0, group, 0o640):
            _deny("PROTECTED_INPUT_FILE_METADATA")
    marker = json.loads((_INPUT / "INPUT_SNAPSHOT.json").read_text("utf-8"))
    if (
        marker.get("state") != "PROTECTED_LEGACY_EXIT_INPUT_ONLY_NOT_RECOVERABLE"
        or marker.get("source_commit") != OLD_EXIT_COMMIT
        or marker.get("archive_sha256") != _SOURCE_ARCHIVE_SHA
        or marker.get("systemd_unit_sha256") != _OLD_UNIT_SHA
        or marker.get("rollback_ready") is not False
    ):
        _deny("PROTECTED_INPUT_MARKER")
    vz = _VENV.lstat()
    if not stat.S_ISDIR(vz.st_mode) or vz.st_uid != actor:
        _deny("ISOLATED_VENV_WRONG_ACTOR")
    return _prepare_legacy_fixture(
        archive=_INPUT / "legacy_runtime.tar",
        source_unit=_INPUT / "legacy_unit.service",
        isolated_venv=_VENV,
        rollback_parent=_ROOT,
        lock_path=_LOCK,
        expected_archive_sha256=_SOURCE_ARCHIVE_SHA,
        expected_venv_fingerprint=_ISOLATED_VENV_FINGERPRINT,
        owner_uid=0,
        service_gid=group,
    )
