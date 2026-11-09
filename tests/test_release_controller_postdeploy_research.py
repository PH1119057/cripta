"""Executable post-deploy identity race regressions for Issue #470.

No GitHub API, filesystem deployment, DB, Exchange or service actions.
The real Release Controller module is executed with inert injected ports.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_PATH = ROOT / "operations/infrastructure/cripta-release-controller"
TARGET = "a" * 40
RESEARCH_HEAD = "b" * 40
TREE = "c" * 40


@pytest.fixture
def controller():
    """Compile the actual production functions without Linux-only fcntl.

    Tests execute the exact AST nodes of _postcheck and its guarded helper,
    while all filesystem/Git/GitHub ports remain inert on every CI platform.
    """
    parsed = ast.parse(CONTROLLER_PATH.read_text(encoding="utf-8"))
    needed = {
        "ControllerError",
        "_verify_post_deploy_research_source_advance",
        "_postcheck",
    }
    statements = []
    for node in parsed.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.ClassDef))
            and node.name in needed
        ):
            statements.append(node)
        elif isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name)
                and target.id == "POST_DEPLOY_RESEARCH_ONLY_PREFIXES"
                for target in node.targets
            ):
                statements.append(node)
    assert len(statements) == 4
    actual = ast.Module(body=statements, type_ignores=[])
    module = ModuleType("cripta_release_controller_postdeploy_test")
    module.RUNTIME_ROOT = Path("/isolated/runtime")
    module.TOOLING_ROOT = Path("/isolated/tooling")
    exec(compile(actual, str(CONTROLLER_PATH), "exec"), module.__dict__)
    return module


def configure(
    controller, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *,
    source: str = RESEARCH_HEAD,
    remote: str = RESEARCH_HEAD,
    runtime: str = TARGET,
    tooling: str = TARGET,
    state_commit: str = TARGET,
    clean: bool = True,
    ancestor: str = TARGET,
    local_tree: str = TREE,
    remote_tree: str = TREE,
    paths: tuple[str, ...] = (
        "scripts/research/independent_p90_probe.py",
        "tests/research/test_independent_p90_probe.py",
    ),
):
    facts = SimpleNamespace(
        source=source, remote=remote, runtime=runtime, tooling=tooling,
        state_commit=state_commit, clean=clean, ancestor=ancestor,
        local_tree=local_tree, remote_tree=remote_tree, paths=paths,
        git_calls=[], main_reads=0, source_reads=0,
    )
    (tmp_path / "INSTALLED_COMMIT").write_text(
        facts.state_commit + "\n", encoding="utf-8",
    )
    monkeypatch.setattr(controller, "RELEASE_STATE", tmp_path)

    def current_release(root):
        if root == controller.RUNTIME_ROOT:
            return facts.runtime, root / "releases" / facts.runtime
        if root == controller.TOOLING_ROOT:
            return facts.tooling, root / "releases" / facts.tooling
        raise AssertionError(f"unrecognized release root: {root}")

    def source_state():
        facts.source_reads += 1
        return facts.source, facts.clean

    def current_main():
        facts.main_reads += 1
        return facts.remote, facts.remote_tree

    def git(*args):
        facts.git_calls.append(args)
        if args == ("rev-parse", f"{facts.source}^{{tree}}"):
            return facts.local_tree
        if args == ("merge-base", TARGET, facts.source):
            return facts.ancestor
        if args == (
            "diff", "--no-renames", "--name-only", "-z",
            TARGET, facts.source, "--",
        ):
            return "".join(path + "\0" for path in facts.paths)
        raise AssertionError(f"unexpected git read: {args}")

    monkeypatch.setattr(controller, "_commit_from_current", current_release)
    monkeypatch.setattr(controller, "_source_state", source_state)
    monkeypatch.setattr(controller, "current_main", current_main)
    monkeypatch.setattr(controller, "_git_remote_main", lambda: facts.remote)
    monkeypatch.setattr(controller, "_git", git)
    return facts


def test_exact_original_release_has_no_new_drift_requirement(
    controller, monkeypatch, tmp_path,
):
    facts = configure(
        controller, monkeypatch, tmp_path, source=TARGET, remote=TARGET,
    )
    assert controller._postcheck(TARGET) == (TARGET, TARGET, TARGET, TARGET)
    assert facts.git_calls == []


def test_actual_research_only_source_main_fast_forward_is_accepted(
    controller, monkeypatch, tmp_path,
):
    facts = configure(controller, monkeypatch, tmp_path)
    assert controller._postcheck(TARGET) == (
        RESEARCH_HEAD, RESEARCH_HEAD, TARGET, TARGET,
    )
    assert ("merge-base", TARGET, RESEARCH_HEAD) in facts.git_calls
    assert any("--no-renames" in call and "-z" in call for call in facts.git_calls)
    assert facts.main_reads == 2
    assert facts.source_reads == 2


@pytest.mark.parametrize("paths", [
    ("src/bybit_workbench/universal_entry/engine.py",),
    ("operations/monitoring/universal_entry_shadow.py",),
    ("operations/systemd/cripta-native-public-trade-source.service",),
    (".github/workflows/quality.yml",),
    ("scripts/production/deploy.py",),
    ("operations/dashboard/app.py",),
    ("tests/test_release_controller_gate.py",),
    ("docs/DEVELOPMENT_RELEASE_RULES_RU.md",),
    ("scripts/research/safe.py", "src/trading/unsafe.py"),
    ("scripts/research/",),
    (),
])
def test_unreviewed_or_broader_source_advances_fail_closed(
    controller, monkeypatch, tmp_path, paths,
):
    configure(controller, monkeypatch, tmp_path, paths=paths)
    with pytest.raises(
        controller.ControllerError, match="POST_DEPLOY_UNSAFE_SOURCE_DELTA",
    ):
        controller._postcheck(TARGET)


@pytest.mark.parametrize("different", [
    {"runtime": RESEARCH_HEAD},
    {"tooling": RESEARCH_HEAD},
    {"state_commit": RESEARCH_HEAD},
    {"clean": False},
])
def test_research_only_advance_never_overrides_immutable_release_identity(
    controller, monkeypatch, tmp_path, different,
):
    configure(controller, monkeypatch, tmp_path, **different)
    with pytest.raises(
        controller.ControllerError, match="POST_DEPLOY_IDENTITY_MISMATCH",
    ):
        controller._postcheck(TARGET)


@pytest.mark.parametrize("different,expected", [
    ({"remote": TARGET}, "POST_DEPLOY_SOURCE_REMOTE_DIVERGENCE"),
    ({"local_tree": "d" * 40}, "POST_DEPLOY_SOURCE_TREE_MISMATCH"),
    ({"ancestor": "d" * 40}, "POST_DEPLOY_SOURCE_NOT_DESCENDANT"),
])
def test_post_deploy_source_provenance_is_exact(
    controller, monkeypatch, tmp_path, different, expected,
):
    configure(controller, monkeypatch, tmp_path, **different)
    with pytest.raises(controller.ControllerError, match=expected):
        controller._postcheck(TARGET)


def test_remote_move_during_check_is_not_certified(
    controller, monkeypatch, tmp_path,
):
    facts = configure(controller, monkeypatch, tmp_path)
    def moving_main():
        facts.main_reads += 1
        if facts.main_reads == 1:
            return RESEARCH_HEAD, TREE
        return "d" * 40, "e" * 40
    monkeypatch.setattr(controller, "current_main", moving_main)
    with pytest.raises(
        controller.ControllerError,
        match="POST_DEPLOY_REMOTE_MOVED_DURING_VERIFY",
    ):
        controller._postcheck(TARGET)


def test_local_source_move_during_check_is_not_certified(
    controller, monkeypatch, tmp_path,
):
    facts = configure(controller, monkeypatch, tmp_path)
    def moving_source():
        facts.source_reads += 1
        if facts.source_reads == 1:
            return RESEARCH_HEAD, True
        return "d" * 40, True
    monkeypatch.setattr(controller, "_source_state", moving_source)
    with pytest.raises(
        controller.ControllerError,
        match="POST_DEPLOY_SOURCE_MOVED_DURING_VERIFY",
    ):
        controller._postcheck(TARGET)
