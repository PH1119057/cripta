"""Trading-scoped source/live identity classification.

Owner decision #381 / defect #380:
GitHub/source HEAD may advance without changing the installed trading runtime.
Only explicitly classified documentary/research/test paths and an independently
verified/deployed Dashboard presentation bundle may be ignored by trading
SOURCE_LIVE_IDENTITY. Everything else remains fail-closed.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
DOCUMENTARY_PREFIXES = ("docs/", "research/", "tests/")
DOCUMENTARY_EXACT = {"README.md", "AGENTS.md"}
DASHBOARD_PREFIX = "operations/dashboard/"


@dataclass(frozen=True, slots=True)
class SourceLiveIdentity:
    ok: bool
    blocker: str | None
    loaded_commit: str
    source_head: str | None
    remote_head: str | None
    dashboard_ui_commit: str | None
    changed_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def classify_delta_paths(
    changed_paths: list[str] | tuple[str, ...],
    *,
    dashboard_delta_verified: bool,
) -> tuple[bool, str | None]:
    """Pure path classifier for DEVELOPMENT_RELEASE §26.2."""
    for path in changed_paths:
        if path in DOCUMENTARY_EXACT or path.startswith(DOCUMENTARY_PREFIXES):
            continue
        if path.startswith(DASHBOARD_PREFIX):
            if dashboard_delta_verified:
                continue
            return False, "DASHBOARD_UI_DELTA_NOT_VERIFIED"
        return False, "TRADING_OR_UNKNOWN_SOURCE_DELTA"
    return True, None


def _git(source: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(source), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("SOURCE_LIVE_IDENTITY_GIT_FAILED") from exc


def _git_is_ancestor(source: Path, ancestor: str, descendant: str) -> bool:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise RuntimeError("SOURCE_LIVE_IDENTITY_GIT_FAILED") from exc
    return result.returncode == 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dashboard_deploy_evidence(
    *,
    loaded_commit: str,
    source_head: str,
    source: Path,
    dashboard_root: Path,
    release_state: Path,
) -> tuple[bool, str | None]:
    state_marker = release_state / "DASHBOARD_UI_COMMIT"
    current_marker = dashboard_root / "current" / "DASHBOARD_UI_COMMIT"
    try:
        state_commit = state_marker.read_text(encoding="utf-8").strip()
        current_commit = current_marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False, None
    if (
        not SHA.fullmatch(state_commit)
        or current_commit != state_commit
        or not SHA.fullmatch(current_commit)
    ):
        return False, current_commit if SHA.fullmatch(current_commit) else None

    dashboard_commit = current_commit
    if dashboard_commit == loaded_commit:
        return True, dashboard_commit
    if not _git_is_ancestor(source, loaded_commit, dashboard_commit):
        return False, dashboard_commit
    if not _git_is_ancestor(source, dashboard_commit, source_head):
        return False, dashboard_commit

    post_dashboard_delta = _git(
        source,
        "diff",
        "--name-only",
        f"{dashboard_commit}..{source_head}",
        "--",
        "operations/dashboard",
    )
    if post_dashboard_delta:
        return False, dashboard_commit

    deploy_path = release_state / "DASHBOARD_UI_DEPLOY.json"
    try:
        deploy = json.loads(deploy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False, dashboard_commit
    if deploy.get("dashboard_ui_commit") != dashboard_commit:
        return False, dashboard_commit

    html_source = deploy.get("html_source_sha256")
    html_live = deploy.get("html_live_sha256")
    app_source = deploy.get("app_source_sha256")
    app_live = deploy.get("app_live_sha256")
    if (
        not isinstance(html_source, str)
        or not isinstance(html_live, str)
        or not isinstance(app_source, str)
        or not isinstance(app_live, str)
        or html_source != html_live
        or app_source != app_live
    ):
        return False, dashboard_commit

    current_html = dashboard_root / "current" / "index.html"
    current_app = dashboard_root / "current" / "app.py"
    try:
        if _sha256(current_html) != html_live or _sha256(current_app) != app_live:
            return False, dashboard_commit
    except OSError:
        return False, dashboard_commit
    return True, dashboard_commit


def inspect_source_live_identity(
    *,
    loaded_commit: str,
    source: Path = Path("/srv/cripta/source_checkout"),
    dashboard_root: Path = Path("/srv/cripta/dashboard-ui"),
    release_state: Path = Path("/var/lib/cripta/release"),
) -> SourceLiveIdentity:
    """Inspect current Git/source and independent Dashboard identity fail-closed."""
    if not SHA.fullmatch(loaded_commit):
        return SourceLiveIdentity(
            False, "INSTALLED_COMMIT_INVALID", loaded_commit, None, None, None, ()
        )
    try:
        source_head = _git(source, "rev-parse", "HEAD")
        remote_line = _git(source, "ls-remote", "origin", "refs/heads/main")
        remote_head = remote_line.split()[0] if remote_line else ""
        dirty = _git(source, "status", "--porcelain=v1", "--untracked-files=all")
    except RuntimeError:
        return SourceLiveIdentity(
            False, "GIT_IDENTITY_UNAVAILABLE", loaded_commit, None, None, None, ()
        )
    if not SHA.fullmatch(source_head) or not SHA.fullmatch(remote_head):
        return SourceLiveIdentity(
            False,
            "GIT_IDENTITY_INVALID",
            loaded_commit,
            source_head or None,
            remote_head or None,
            None,
            (),
        )
    if dirty:
        return SourceLiveIdentity(
            False,
            "SOURCE_WORKTREE_DIRTY",
            loaded_commit,
            source_head,
            remote_head,
            None,
            (),
        )
    if source_head != remote_head:
        return SourceLiveIdentity(
            False,
            "REMOTE_SOURCE_IDENTITY_DRIFT",
            loaded_commit,
            source_head,
            remote_head,
            None,
            (),
        )
    if source_head == loaded_commit:
        return SourceLiveIdentity(
            True, None, loaded_commit, source_head, remote_head, loaded_commit, ()
        )
    try:
        if not _git_is_ancestor(source, loaded_commit, source_head):
            return SourceLiveIdentity(
                False,
                "INSTALLED_NOT_ANCESTOR_OF_SOURCE",
                loaded_commit,
                source_head,
                remote_head,
                None,
                (),
            )
        changed_raw = _git(
            source, "diff", "--name-only", f"{loaded_commit}..{source_head}"
        )
        changed_paths = tuple(line for line in changed_raw.splitlines() if line)
        has_dashboard_delta = any(
            path.startswith(DASHBOARD_PREFIX) for path in changed_paths
        )
        dashboard_verified = False
        dashboard_commit: str | None = None
        if has_dashboard_delta:
            dashboard_verified, dashboard_commit = _dashboard_deploy_evidence(
                loaded_commit=loaded_commit,
                source_head=source_head,
                source=source,
                dashboard_root=dashboard_root,
                release_state=release_state,
            )
        allowed, reason = classify_delta_paths(
            changed_paths,
            dashboard_delta_verified=dashboard_verified,
        )
    except RuntimeError:
        return SourceLiveIdentity(
            False,
            "GIT_IDENTITY_UNAVAILABLE",
            loaded_commit,
            source_head,
            remote_head,
            None,
            (),
        )

    return SourceLiveIdentity(
        allowed,
        reason,
        loaded_commit,
        source_head,
        remote_head,
        dashboard_commit,
        changed_paths,
    )
