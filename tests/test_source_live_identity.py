from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bybit_workbench import source_live_identity as identity


LOADED = "a" * 40
SOURCE = "b" * 40


def test_path_classifier_allows_documentary_delta() -> None:
    assert identity.classify_delta_paths(
        ["docs/X.md", "research/result.json", "tests/test_x.py", "README.md"],
        dashboard_delta_verified=False,
    ) == (True, None)


def test_path_classifier_allows_only_verified_dashboard_delta() -> None:
    paths = ["operations/dashboard/index.html", "tests/test_dashboard.py"]
    assert identity.classify_delta_paths(
        paths, dashboard_delta_verified=True
    ) == (True, None)
    assert identity.classify_delta_paths(
        paths, dashboard_delta_verified=False
    ) == (False, "DASHBOARD_UI_DELTA_NOT_VERIFIED")


def test_path_classifier_blocks_trading_release_and_unknown_delta() -> None:
    for path in (
        "src/bybit_workbench/live_arm.py",
        "operations/implementation/control.py",
        "operations/infrastructure/install_verified_release.sh",
        "pyproject.toml",
    ):
        assert identity.classify_delta_paths(
            [path], dashboard_delta_verified=True
        ) == (False, "TRADING_OR_UNKNOWN_SOURCE_DELTA")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dashboard_evidence(tmp_path: Path) -> tuple[Path, Path]:
    dashboard = tmp_path / "dashboard-ui"
    current = dashboard / "current"
    current.mkdir(parents=True)
    (current / "DASHBOARD_UI_COMMIT").write_text(SOURCE + "\n", encoding="utf-8")
    (current / "index.html").write_text("<html>verified</html>", encoding="utf-8")
    (current / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    state = tmp_path / "release"
    state.mkdir()
    (state / "DASHBOARD_UI_COMMIT").write_text(SOURCE + "\n", encoding="utf-8")
    (state / "DASHBOARD_UI_DEPLOY.json").write_text(
        json.dumps(
            {
                "dashboard_ui_commit": SOURCE,
                "html_source_sha256": _hash(current / "index.html"),
                "html_live_sha256": _hash(current / "index.html"),
                "app_source_sha256": _hash(current / "app.py"),
                "app_live_sha256": _hash(current / "app.py"),
            }
        ),
        encoding="utf-8",
    )
    return dashboard, state


def test_inspect_accepts_verified_deployed_dashboard_delta(
    monkeypatch, tmp_path: Path
) -> None:
    dashboard, state = _dashboard_evidence(tmp_path)

    def fake_git(_source: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return SOURCE
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return SOURCE + "\trefs/heads/main"
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return ""
        if args == ("diff", "--name-only", f"{LOADED}..{SOURCE}"):
            return "operations/dashboard/index.html\ntests/test_dashboard.py"
        if args == (
            "diff",
            "--name-only",
            f"{SOURCE}..{SOURCE}",
            "--",
            "operations/dashboard",
        ):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(identity, "_git", fake_git)
    monkeypatch.setattr(identity, "_git_is_ancestor", lambda *_args: True)

    result = identity.inspect_source_live_identity(
        loaded_commit=LOADED,
        source=tmp_path / "source",
        dashboard_root=dashboard,
        release_state=state,
    )
    assert result.ok is True
    assert result.blocker is None
    assert result.dashboard_ui_commit == SOURCE


def test_inspect_blocks_dashboard_delta_without_deploy_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dashboard, state = _dashboard_evidence(tmp_path)
    (state / "DASHBOARD_UI_DEPLOY.json").unlink()

    def fake_git(_source: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return SOURCE
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return SOURCE + "\trefs/heads/main"
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return ""
        if args == ("diff", "--name-only", f"{LOADED}..{SOURCE}"):
            return "operations/dashboard/index.html"
        if args == (
            "diff",
            "--name-only",
            f"{SOURCE}..{SOURCE}",
            "--",
            "operations/dashboard",
        ):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(identity, "_git", fake_git)
    monkeypatch.setattr(identity, "_git_is_ancestor", lambda *_args: True)

    result = identity.inspect_source_live_identity(
        loaded_commit=LOADED,
        source=tmp_path / "source",
        dashboard_root=dashboard,
        release_state=state,
    )
    assert result.ok is False
    assert result.blocker == "DASHBOARD_UI_DELTA_NOT_VERIFIED"


def test_inspect_blocks_trading_delta_even_with_dashboard_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dashboard, state = _dashboard_evidence(tmp_path)

    def fake_git(_source: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return SOURCE
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return SOURCE + "\trefs/heads/main"
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return ""
        if args == ("diff", "--name-only", f"{LOADED}..{SOURCE}"):
            return "operations/dashboard/index.html\nsrc/bybit_workbench/engine.py"
        if args == (
            "diff",
            "--name-only",
            f"{SOURCE}..{SOURCE}",
            "--",
            "operations/dashboard",
        ):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(identity, "_git", fake_git)
    monkeypatch.setattr(identity, "_git_is_ancestor", lambda *_args: True)

    result = identity.inspect_source_live_identity(
        loaded_commit=LOADED,
        source=tmp_path / "source",
        dashboard_root=dashboard,
        release_state=state,
    )
    assert result.ok is False
    assert result.blocker == "TRADING_OR_UNKNOWN_SOURCE_DELTA"
