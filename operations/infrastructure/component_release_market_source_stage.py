"""#865 / #849: prepare immutable independent MarketSource component (no cutover).

One reviewed exact source SHA; legacy runtime and foreign unit remain untouched.
No DB, Exchange, systemd, source checkout or global runtime mutations here.
The separate authenticated no-argument unit-transition owner is the only actor
that may later change the installed unit. Root execution requires sealed source.
"""
from __future__ import annotations

import grp
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

SOURCE_SHA = "c5d355b02d59aa068d7858bd0428ebd9788e7e19"
LEGACY_SHA = "8bd45e9874d6ce811d7261ee152fc2c43729e1b9"
OLD_UNIT_SHA = "e571e42798a9d35e1d951c8e7e42ace9aab21a8a6b7823a054ad49048e88452a"
NEW_ADAPTER_SHA = "a4fd0d3336cf7506a0857e5389450e6a637d0052ac2efdb3a17f3f23bbae3a88"
OLD_ADAPTER_SHA = "71e0561b0c237c0b5251d409205f6093f64a02f79f46c58fb773a4484d667a74"
_BASE = Path("/srv/cripta/runtime/releases") / LEGACY_SHA
_SOURCE = Path("/srv/cripta/source_checkout")
_OWNER = Path("/srv/cripta/components/market_source")
_ROOT = _OWNER / "releases" / SOURCE_SHA
_UNIT_PATH = Path("/etc/systemd/system/cripta-native-public-trade-source.service")
_ADAPTER = "operations/monitoring/public_market_source_union_capture.py"
_MANIFEST = "COMPONENT_SOURCE_MANIFEST.json"
_CONTROLLER_ROOT = _OWNER / ".owner-controller-865"
_CONTROLLER_FILES = frozenset({
    "component_release_market_source_stage.py",
    "component_release_market_source_transition.py",
})
_CONTROLLER_MANIFEST = "SOURCE_MANIFEST.json"
_OLD_MARKER = "INSTALLED_COMMIT"
_SHA40 = re.compile(r"[a-f0-9]{40}\Z")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _deny(code: str) -> None:
    raise RuntimeError("MARKET_SOURCE_COMPONENT_" + code)


def _expected_dir(path: Path, *, uid: int, gid: int, mode: int) -> None:
    st = path.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != uid
            or st.st_gid != gid or stat.S_IMODE(st.st_mode) != mode):
        _deny("PARENT_OWNER_OR_MODE_DRIFT")


def _old_unit() -> bytes:
    raw = _UNIT_PATH.read_bytes()
    if _digest(raw) != OLD_UNIT_SHA:
        _deny("OLD_UNIT_DRIFT")
    return raw


def pinned_market_source_unit(original: bytes, *, root: Path = _ROOT) -> bytes:
    """Pure exact unit rewrite; no global release.env/ExecStart shell."""
    if _digest(original) != OLD_UNIT_SHA or not root.is_absolute():
        _deny("UNIT_TEMPLATE_UNVERIFIED")
    old = "/srv/cripta/runtime/current"
    text = original.decode("utf-8")
    required = (
        "Environment=PYTHONPATH=" + old + "/src:" + old,
        "WorkingDirectory=" + old,
        "ExecStart=" + old + "/.venv/bin/python " + old
        + "/operations/monitoring/native_public_trade_source.py",
        "ReadOnlyPaths=" + old,
    )
    if any(text.count(line) != 1 for line in required):
        _deny("UNIT_OWNERSHIP_SCOPE_DRIFT")
    # Use the exact component-local Python and source closure. The old global
    # release.env is preserved unchanged for other units and private settings.
    # EnvironmentFile overrides Environment, therefore ExecStart's fixed env
    # assignment is the only safe source-commit override for this process.
    text = text.replace(
        required[2],
        "ExecStart=/usr/bin/env CRIPTA_RELEASE_COMMIT=" + SOURCE_SHA
        + " " + str(root) + "/.venv/bin/python "
        + str(root) + "/operations/monitoring/native_public_trade_source.py",
    )
    text = text.replace(required[0], "Environment=PYTHONPATH="
                        + str(root) + "/src:" + str(root))
    text = text.replace(required[1], "WorkingDirectory=" + str(root))
    text = text.replace(required[3], "ReadOnlyPaths=" + str(root))
    if old in text or "EnvironmentFile=/etc/cripta/release.env" not in text:
        _deny("UNIT_UNEXPECTED_GLOBAL_RUNTIME_REFERENCE")
    return text.encode("utf-8")


def _source_exact() -> bytes:
    # Source mirror is allowed as a read-only Git checkout only.
    check = subprocess.run(
        ["/usr/sbin/runuser", "-u", "cripta", "--", "/usr/bin/env",
         "GIT_OPTIONAL_LOCKS=0", "/usr/bin/git", "-C", str(_SOURCE),
         "rev-parse", "HEAD"], capture_output=True, text=True, timeout=8,
    )
    if (check.returncode or _SHA40.fullmatch(check.stdout.strip()) is None):
        _deny("SOURCE_MAIN_SHA_INVALID")
    # A reviewed staging-tool PR is necessarily merged AFTER the executable
    # component commit. Keep code provenance pinned to SOURCE_SHA but accept
    # only a descendant HEAD whose live MarketSource/ABI inputs are identical.
    head = check.stdout.strip()
    ancestor = subprocess.run(
        ["/usr/sbin/runuser", "-u", "cripta", "--", "/usr/bin/env",
         "GIT_OPTIONAL_LOCKS=0", "/usr/bin/git", "-C", str(_SOURCE),
         "merge-base", "--is-ancestor", SOURCE_SHA, head],
        capture_output=True, timeout=8,
    )
    if ancestor.returncode:
        _deny("SOURCE_COMMIT_NOT_ANCESTOR")
    unchanged = subprocess.run(
        ["/usr/sbin/runuser", "-u", "cripta", "--", "/usr/bin/env",
         "GIT_OPTIONAL_LOCKS=0", "/usr/bin/git", "-C", str(_SOURCE),
         "diff", "--name-only", SOURCE_SHA, head, "--",
         _ADAPTER,
         "operations/monitoring/native_public_trade_source.py",
         "src/bybit_workbench/market_data/native_public_trade_journal.py",
         "src/bybit_workbench/market_identity.py",
         "operations/systemd/cripta-native-public-trade-source.service"],
        capture_output=True, timeout=10,
    )
    if unchanged.returncode or unchanged.stdout.strip():
        _deny("SOURCE_EXECUTABLE_TREE_CHANGED_AFTER_PIN")
    dirty = subprocess.run(
        ["/usr/sbin/runuser", "-u", "cripta", "--", "/usr/bin/env",
         "GIT_OPTIONAL_LOCKS=0", "/usr/bin/git", "-C", str(_SOURCE),
         "status", "--porcelain=v1"], capture_output=True, text=True, timeout=10,
    )
    if dirty.returncode or dirty.stdout.strip():
        _deny("SOURCE_MIRROR_DIRTY")
    latest = (_SOURCE / _ADAPTER).read_bytes()
    old = (_BASE / _ADAPTER).read_bytes()
    if _digest(latest) != NEW_ADAPTER_SHA or _digest(old) != OLD_ADAPTER_SHA:
        _deny("EXACT_ADAPTER_BYTES_DRIFT")
    if (_BASE / _OLD_MARKER).read_text(encoding="utf-8").strip() != LEGACY_SHA:
        _deny("LEGACY_INSTALLED_IDENTITY_DRIFT")
    return latest


def _walk_digest(root: Path) -> tuple[str, int]:
    """Full physical code+venv closure, rejects external moving symlinks."""
    h = hashlib.sha256()
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        for name in dirs + files:
            p = Path(directory) / name
            rel = p.relative_to(root).as_posix()
            if rel == _MANIFEST:
                continue
            st = p.lstat()
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISDIR(st.st_mode):
                kind, detail = "D", ""
            elif stat.S_ISREG(st.st_mode):
                kind, detail = "F", _digest(p.read_bytes())
            elif stat.S_ISLNK(st.st_mode):
                kind = "L"
                detail = os.readlink(p)
                # Only the copied Python venv may link to its own contents
                # or a fixed system interpreter.
                if not rel.startswith(".venv/") or (
                    detail.startswith("/") and detail != "/usr/bin/python3"
                ):
                    _deny("SOURCE_SYMLINK_OUTSIDE_DEPENDENCY_CLOSURE")
            else:
                _deny("SOURCE_SPECIAL_FILE_UNSUPPORTED")
            h.update(json.dumps(
                [rel, kind, mode, st.st_size if kind == "F" else 0, detail],
                separators=(",", ":"), ensure_ascii=True,
            ).encode("utf-8") + b"\n")
            total += 1
    return h.hexdigest(), total


def _seal_tree(root: Path, *, gid: int) -> None:
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(directory) / name
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                os.lchown(p, 0, gid)
            else:
                os.chown(p, 0, gid)
                new_mode = 0o550 if stat.S_ISDIR(st.st_mode) else (
                    0o550 if st.st_mode & 0o111 else 0o440
                )
                os.chmod(p, new_mode)
    os.chown(root, 0, gid)
    os.chmod(root, 0o550)


def inspect_prepared_market_source() -> dict[str, object]:
    if not _ROOT.is_dir() or _ROOT.is_symlink():
        _deny("CANDIDATE_NOT_PREPARED")
    manifest_file = _ROOT / _MANIFEST
    meta = json.loads(manifest_file.read_text(encoding="utf-8"))
    if (set(meta) != {
        "schema", "source_commit", "legacy_dependency_commit", "union_sha256",
        "old_unit_sha256", "candidate_unit_sha256", "content_tree_sha256",
        "content_entry_count", "status",
    } or meta["schema"] != "MARKET_SOURCE_COMPONENT_865_V1"
        or meta["source_commit"] != SOURCE_SHA
        or meta["legacy_dependency_commit"] != LEGACY_SHA
        or meta["union_sha256"] != NEW_ADAPTER_SHA
        or meta["old_unit_sha256"] != OLD_UNIT_SHA
        or meta["status"] != "STAGED_ONLY_NOT_ACTIVATED"
        or (_ROOT / "INSTALLED_COMMIT").read_text(encoding="utf-8").strip()
            != SOURCE_SHA
        or type(meta["content_entry_count"]) is not int
        or meta["content_entry_count"] < 100):
        _deny("CANDIDATE_MANIFEST_INVALID")
    actual, count = _walk_digest(_ROOT)
    if actual != meta["content_tree_sha256"] or count != meta["content_entry_count"]:
        _deny("CANDIDATE_PHYSICAL_TREE_HASH_DRIFT")
    if _digest((_ROOT / "pinned-market-source.service").read_bytes()) != meta["candidate_unit_sha256"]:
        _deny("CANDIDATE_UNIT_HASH_DRIFT")
    st = _ROOT.lstat()
    if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o550:
        _deny("CANDIDATE_ROOT_OWNER_DRIFT")
    return {"state":"MARKET_SOURCE_CANDIDATE_STAGED_VERIFIED",
            "source_commit":SOURCE_SHA,"tree_sha256":actual,"file_entries":count,
            "unit_sha256":meta["candidate_unit_sha256"],"cutover_authorized":False}


def verify_sealed_market_source_controller() -> None:
    """Controller imports must be from exact root-owned Git-authored bytes."""
    import sys
    sys.dont_write_bytecode = True
    gid = grp.getgrnam("cripta").gr_gid
    _expected_dir(_CONTROLLER_ROOT, uid=0, gid=gid, mode=0o550)
    names = {p.name for p in _CONTROLLER_ROOT.iterdir()}
    if names != _CONTROLLER_FILES | {_CONTROLLER_MANIFEST}:
        _deny("ROOT_CONTROLLER_FILE_SET_DRIFT")
    manifest_file = _CONTROLLER_ROOT / _CONTROLLER_MANIFEST
    st = manifest_file.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0
            or st.st_gid != 0 or stat.S_IMODE(st.st_mode) != 0o400):
        _deny("ROOT_CONTROLLER_MANIFEST_OWNER_DRIFT")
    m = json.loads(manifest_file.read_text(encoding="utf-8"))
    if (set(m) != {"schema", "source_commit", "files"}
            or m["schema"] != "MARKET_SOURCE_ROOT_CONTROLLER_865_V1"
            or m["source_commit"] != SOURCE_SHA
            or not isinstance(m["files"],dict)
            or set(m["files"]) != _CONTROLLER_FILES):
        _deny("ROOT_CONTROLLER_MANIFEST_INVALID")
    for name, expected in m["files"].items():
        p = _CONTROLLER_ROOT / name
        z = p.lstat()
        if (not stat.S_ISREG(z.st_mode) or z.st_uid != 0
                or z.st_gid != gid or stat.S_IMODE(z.st_mode) != 0o440
                or not isinstance(expected,str) or len(expected) != 64
                or _digest(p.read_bytes()) != expected):
            _deny("ROOT_CONTROLLER_SOURCE_HASH_DRIFT")


def stage_market_source_component_once() -> dict[str, object]:
    """Root-only, bounded staging; NO systemd, DB, Exchange or live service."""
    if os.geteuid() != 0:
        _deny("ROOT_REQUIRED")
    verify_sealed_market_source_controller()
    gid = grp.getgrnam("cripta").gr_gid
    _expected_dir(Path("/srv/cripta"),uid=0,gid=0,mode=0o755)
    _expected_dir(Path("/srv/cripta/components"),uid=0,gid=gid,mode=0o750)
    updated = _source_exact()
    unit = pinned_market_source_unit(_old_unit())
    if _ROOT.exists() or _ROOT.is_symlink():
        return inspect_prepared_market_source()
    # New parent children only. Never mutate /srv/cripta/runtime/current.
    for folder in (_OWNER, _OWNER / "releases", _OWNER / "journal",
                   _OWNER / "rollback"):
        if folder.exists() or folder.is_symlink():
            _expected_dir(folder,uid=0,gid=gid,mode=0o750)
        else:
            folder.mkdir(mode=0o750)
            os.chown(folder,0,gid)
    scratch = _ROOT.with_name(".pending-" + SOURCE_SHA)
    if scratch.exists() or scratch.is_symlink():
        _deny("PREVIOUS_UNKNOWN_STAGING_OUTCOME")
    scratch.mkdir(mode=0o750)
    os.chown(scratch,0,gid)
    try:
        for dirname in ("operations","src",".venv"):
            shutil.copytree(_BASE / dirname,scratch / dirname,symlinks=True)
        # The legacy Dashboard presentation symlink is an UNRELATED mutable
        # external owner. It cannot enter the MarketSource code closure.
        external = scratch / "operations/dashboard/index.html"
        if not external.is_symlink() or os.readlink(external) != (
            "/srv/cripta/dashboard-ui/current/index.html"
        ):
            _deny("LEGACY_DASHBOARD_SYMLINK_DRIFT")
        external.unlink()
        (scratch / _ADAPTER).write_bytes(updated)
        (scratch / "pinned-market-source.service").write_bytes(unit)
        # The immutable candidate identity is required by the canonical
        # append-only source compatibility attestor. Manifest status still
        # says STAGED_ONLY_NOT_ACTIVATED: this is NOT loaded runtime evidence.
        (scratch / "INSTALLED_COMMIT").write_text(
            SOURCE_SHA+"\n", encoding="utf-8"
        )
        _seal_tree(scratch,gid=gid)
        digest, count = _walk_digest(scratch)
        meta = {
            "schema":"MARKET_SOURCE_COMPONENT_865_V1",
            "source_commit":SOURCE_SHA,
            "legacy_dependency_commit":LEGACY_SHA,
            "union_sha256":NEW_ADAPTER_SHA,
            "old_unit_sha256":OLD_UNIT_SHA,
            "candidate_unit_sha256":_digest(unit),
            "content_tree_sha256":digest,
            "content_entry_count":count,
            "status":"STAGED_ONLY_NOT_ACTIVATED",
        }
        manifest = scratch / _MANIFEST
        manifest.write_text(
            json.dumps(meta,sort_keys=True,separators=(",",":"))+"\n",
            encoding="utf-8",
        )
        os.chown(manifest,0,gid)
        os.chmod(manifest,0o440)
        scratch.rename(_ROOT)
        dirfd = os.open(_OWNER / "releases",os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    except BaseException:
        # Unknown partial state is not retried automatically. Preserve its
        # exact .pending path for owned forensic reconciliation.
        raise
    return inspect_prepared_market_source()


if __name__ == "__main__":
    import sys
    sys.dont_write_bytecode = True
    if (len(sys.argv)!=1 or Path(sys.argv[0]) != (
        _CONTROLLER_ROOT / "component_release_market_source_stage.py"
    )):
        raise SystemExit("MARKET_SOURCE_STAGER_SEALED_NO_ARGUMENTS_ONLY")
    print(json.dumps(stage_market_source_component_once(),sort_keys=True))
