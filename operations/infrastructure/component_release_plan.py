"""Deterministic source-delta ownership for independently releasable CRIPTA units.

Issue #849 / ARCH §50.1 / DEV §50.6. Pure classifier, never a deployment
authorization: a matching source path still needs signed candidate, dependency
closure, SQL/API compatibility, per-unit actor/ACL, runtime and rollback proof.

This module does not read credentials, connect to PostgreSQL, mutate service
state, create a branch, or touch an installed release.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

SHA = re.compile(r"[0-9a-f]{40}\Z")

# Prefixes here are CODE OWNERSHIP candidates, not claims of a compatible
# deployment bundle. Shared imports/ABI are validated at a later gate.
COMPONENT_PATHS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("EXIT", "prefix", ("src/bybit_workbench/universal_exit/",)),
    ("ENTRY", "prefix", ("src/bybit_workbench/universal_entry/",)),
    ("DISPATCHER", "prefix", ("operations/dispatcher_v2/",)),
    ("EXIT", "exact", ("operations/connectivity/universal_exit_consumer.py",)),
    ("ENTRY", "exact", ("operations/connectivity/universal_entry_consumer.py",)),
    ("ENTRY_OBSERVER", "exact", ("operations/monitoring/universal_entry_shadow.py",)),
    ("DASHBOARD_PRESENTATION", "exact", ("operations/dashboard/index.html",)),
)

# Unit instances may be independent even when their Entry implementation is
# shared: a plan is not permission to restart ALL matching instances.
COMPONENT_UNITS: dict[str, tuple[str, ...]] = {
    "EXIT": ("cripta-universal-exit-consumer.service",),
    "ENTRY": (
        "cripta-universal-entry-consumer.service",
        "cripta-universal-entry-consumer@*.service",
    ),
    "DISPATCHER": ("cripta-dispatcher-v2.service",),
    "ENTRY_OBSERVER": ("cripta-universal-entry-observer.service",),
    "DASHBOARD_PRESENTATION": (),
}

NON_RUNTIME_PREFIXES = (
    "docs/", "research/", "tests/", "tests/research/", "scripts/research/",
)
NON_RUNTIME_EXACT = (
    "README.md", "AGENTS.md",
    "operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt",
)

# Mixed authenticated HTTP/mutation/read-model must be split before independent
# Dashboard backend release; SQL belongs to migration actor, NEVER UI release.
CONTROL_OR_SHARED: tuple[tuple[str, str], ...] = (
    ("operations/dashboard/app.py", "MIXED_DASHBOARD_READ_CONTROL"),
    ("operations/sql/", "POSTGRESQL_MIGRATION"),
    ("operations/systemd/", "SERVICE_UNIT_OR_RUNTIME_ABI"),
    ("operations/infrastructure/", "RELEASE_INFRASTRUCTURE"),
    ("src/", "SHARED_OR_UNMAPPED_LIBRARY"),
    ("operations/", "SHARED_OR_UNMAPPED_OPERATION"),
    ("config/", "SHARED_CONFIGURATION"),
    ("engine/", "SHARED_ENGINE"),
    ("pyproject.toml", "SHARED_DEPENDENCIES"),
    ("uv.lock", "SHARED_DEPENDENCIES"),
    (".python-version", "SHARED_DEPENDENCIES"),
    (".github/", "CI_OR_RELEASE_CONTRACT"),
)

@dataclass(frozen=True, slots=True)
class ComponentScopePlan:
    changed_paths: tuple[str, ...]
    components: tuple[str, ...]
    candidate_units: tuple[str, ...]
    release_scope: str
    non_runtime_paths: tuple[str, ...]
    requires_review: tuple[str, ...]
    # A classifier is never an installer or an approval gate.
    install_authorized: bool = False
    database_mutation_authorized: bool = False
    financial_mutation_authorized: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _valid_path(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or "\\" in value or "\x00" in value:
        return False
    if "\n" in value or "\r" in value:
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _owner(path: str) -> tuple[str | None, str | None]:
    if path in NON_RUNTIME_EXACT or path.startswith(NON_RUNTIME_PREFIXES):
        return None, None

    for component, kind, patterns in COMPONENT_PATHS:
        if kind == "exact" and path in patterns:
            return component, None
        if kind == "prefix" and path.startswith(patterns):
            return component, None

    for prefix, blocker in CONTROL_OR_SHARED:
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
            return None, blocker
    return None, "UNKNOWN_PATH_OWNER"


def plan_paths(paths: Iterable[str]) -> ComponentScopePlan:
    """Classify every changed path, never implicitly grant a full restart."""
    source = list(paths)
    if not source:
        raise ValueError("DELTA_EMPTY")
    if any(not _valid_path(path) for path in source):
        raise ValueError("DELTA_PATH_UNSAFE")
    unique = sorted(set(source))
    owners: set[str] = set()
    source_only: list[str] = []
    blockers: set[str] = set()

    for path in unique:
        component, reason = _owner(path)
        if reason:
            blockers.add(reason + ":" + path)
        elif component:
            owners.add(component)
        else:
            source_only.append(path)

    components = tuple(sorted(owners))
    units = tuple(sorted({unit for owner in owners for unit in COMPONENT_UNITS[owner]}))
    if blockers:
        release_scope = "COMPATIBILITY_REVIEW_REQUIRED"
    elif len(components) > 1:
        release_scope = "MULTI_COMPONENT_REVIEW_REQUIRED"
    elif len(components) == 1:
        release_scope = (
            "DASHBOARD_PRESENTATION_CANDIDATE"
            if components == ("DASHBOARD_PRESENTATION",)
            else "COMPONENT_CANDIDATE"
        )
    else:
        release_scope = "SOURCE_ONLY_NO_RUNTIME_DEPLOY"

    return ComponentScopePlan(
        changed_paths=tuple(unique),
        components=components,
        candidate_units=units,
        release_scope=release_scope,
        non_runtime_paths=tuple(source_only),
        requires_review=tuple(sorted(blockers)),
    )


def git_changed_paths(repo: Path, baseline: str, target: str) -> tuple[str, ...]:
    """Read both rename sides via --no-renames; no checkout or Git mutation."""
    if not SHA.fullmatch(baseline) or not SHA.fullmatch(target):
        raise ValueError("SHA_INVALID")
    if not repo.is_dir():
        raise ValueError("REPOSITORY_UNAVAILABLE")
    command = [
        "git", "-C", str(repo), "-c", "core.quotePath=false",
        "diff", "--no-renames", "--name-only", "-z", baseline, target, "--",
    ]
    result = subprocess.run(command, check=False, capture_output=True)
    if result.returncode != 0:
        raise ValueError("GIT_DELTA_UNAVAILABLE")
    if not result.stdout:
        raise ValueError("DELTA_EMPTY")
    try:
        return tuple(path.decode("utf-8") for path in result.stdout.split(b"\x00") if path)
    except UnicodeDecodeError as exc:
        raise ValueError("DELTA_PATH_ENCODING_INVALID") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only CRIPTA component ownership planner")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    try:
        result = plan_paths(git_changed_paths(args.repo, args.baseline, args.target))
    except ValueError as exc:
        print(json.dumps({"release_scope": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
