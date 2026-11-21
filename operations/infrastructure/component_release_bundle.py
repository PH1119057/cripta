"""Stage exact-Git immutable component candidate archives without deploying them.

Issue #849. This module intentionally has NO systemd, PostgreSQL, or LIVE
operation; archived code is not a runnable/approved deployment until its
dependency environment and consumer compatibility are attested separately.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from component_release_plan import git_changed_paths, plan_paths

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_EXIT_ENTRYPOINT = "operations/connectivity/universal_exit_consumer.py"
# Exact local dynamic imports inspected in the installed Exit process.
# Not a wildcard over connectivity: other files contain separate owners.
_EXIT_DYNAMIC_SIBLINGS = (
    "operations/connectivity/p90_real_independent_exit.py",
    "operations/connectivity/p90_real_persistent_trail.py",
    "operations/connectivity/p90_r06_exit_owner.py",
    "operations/connectivity/p90l90_real_postfill_source_client.py",
)
_EXIT_REQUIREMENTS_LOCK = "operations/runtime/runtime_requirements.lock"
_EXIT_REQUIRED = (_EXIT_ENTRYPOINT, *_EXIT_DYNAMIC_SIBLINGS, _EXIT_REQUIREMENTS_LOCK)
_COMPONENT_SOURCES: dict[str, tuple[str, ...]] = {
    "EXIT": ("src/", *_EXIT_REQUIRED),
    "ENTRY": ("src/", "operations/connectivity/universal_entry_consumer.py"),
    "ENTRY_OBSERVER": ("src/", "operations/monitoring/universal_entry_shadow.py"),
    "DISPATCHER": ("src/", "operations/dispatcher_v2/"),
    "DASHBOARD_PRESENTATION": ("operations/dashboard/index.html",),
}


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError("SOURCE_GIT_EVIDENCE_UNAVAILABLE")
    return result.stdout


def _tracked_files(repo: Path, target: str) -> tuple[tuple[str, str], ...]:
    """Git mode+path pairs; reject symlinks/submodules or unknown path encodings."""
    out = _git(repo, "ls-tree", "-r", "-z", target, "--", "src", "operations")
    entries: list[tuple[str, str]] = []
    for raw in out.split(b"\x00"):
        if not raw:
            continue
        try:
            meta, raw_path = raw.split(b"\t", 1)
            mode, kind, _oid = meta.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("BUNDLE_TREE_INVALID") from exc
        # Unrelated paths do not affect this bundle; selected paths are
        # checked for symlinks and unusual modes below.
        entries.append((path, mode if kind == "blob" else "UNSUPPORTED"))
    return tuple(entries)


def stage_component_bundle(
    *, repo: Path, baseline: str, target: str, component: str,
    output: Path,
) -> dict[str, object]:
    """Create one immutable-by-Git candidate ZIP atomically; never activate it."""
    if not _SHA.fullmatch(baseline) or not _SHA.fullmatch(target):
        raise ValueError("BUNDLE_COMMIT_INVALID")
    delta = git_changed_paths(repo, baseline, target)
    plan = plan_paths(delta)
    if (
        plan.release_scope not in ("COMPONENT_CANDIDATE", "DASHBOARD_PRESENTATION_CANDIDATE")
        or plan.components != (component,) or component not in _COMPONENT_SOURCES
    ):
        raise ValueError("BUNDLE_SCOPE_UNPROVEN")
    if output.exists() or output.is_symlink():
        raise ValueError("BUNDLE_OUTPUT_ALREADY_EXISTS")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ValueError("BUNDLE_PARENT_INVALID")

    selectors = _COMPONENT_SOURCES[component]
    tracked = tuple((path, mode) for path, mode in _tracked_files(repo, target)
                    if any(path == selector or path.startswith(selector)
                           for selector in selectors))
    if any(mode not in ("100644", "100755") for _path, mode in tracked):
        raise ValueError("BUNDLE_SYMLINK_OR_UNSUPPORTED_FILE")
    if not tracked:
        raise ValueError("BUNDLE_SOURCES_MISSING")
    if component == "EXIT":
        selected = {path for path, _mode in tracked}
        if any(path not in selected for path in _EXIT_REQUIRED):
            raise ValueError("BUNDLE_EXIT_CLOSURE_MISSING")
    if component != "DASHBOARD_PRESENTATION" and not any(
        path.startswith("src/") for path, _mode in tracked
    ):
        raise ValueError("BUNDLE_PYTHON_SOURCE_MISSING")
    executable_selector = (
        _EXIT_ENTRYPOINT if component == "EXIT" else selectors[-1]
    )
    if not any(path == executable_selector or (
        executable_selector.endswith("/") and path.startswith(executable_selector)
    ) for path, _mode in tracked):
        raise ValueError("BUNDLE_ENTRYPOINT_MISSING")

    files: dict[str, str] = {}
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".cripta-component-", suffix=".zip",
            dir=output.parent, delete=False,
        ) as scratch:
            staged = Path(scratch.name)
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, mode in tracked:
                blob = _git(repo, "show", f"{target}:{path}")
                files[path] = hashlib.sha256(blob).hexdigest()
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (int(mode, 8) << 16)
                archive.writestr(info, blob)
            manifest: dict[str, object] = {
                "schema_version": 1,
                "component": component,
                "baseline": baseline,
                "target": target,
                "delta_paths": list(plan.changed_paths),
                "candidate_units": list(plan.candidate_units),
                "blob_sha256": files,
                "status": "STAGED_ONLY_NOT_DEPLOYABLE",
                "dependency_environment": "NOT_ATTESTED",
                "postgresql_compatibility": "NOT_ATTESTED",
                "install_authorized": False,
            }
            if component == "EXIT":
                manifest.update({
                    "entrypoint": _EXIT_ENTRYPOINT,
                    "dynamic_import_siblings": list(_EXIT_DYNAMIC_SIBLINGS),
                    "dependency_lock": _EXIT_REQUIREMENTS_LOCK,
                    "dependency_lock_sha256": files[_EXIT_REQUIREMENTS_LOCK],
                    # Archive provenance is not a compatible pinned Python
                    # environment, independently installed unit, or rollback.
                    "runtime_dependency_abi": "NOT_ATTESTED",
                    "unit_pinning": "NOT_ATTESTED",
                })
            info = zipfile.ZipInfo("component_manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o444 << 16
            archive.writestr(info, json.dumps(
                manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8"))
        # Windows requires a writable descriptor for fsync; the archive
        # stays a staged-only candidate and no installed file is touched.
        with staged.open("r+b") as file:
            os.fsync(file.fileno())
        os.chmod(staged, 0o444)
        os.replace(staged, output)
        staged = None
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return manifest
