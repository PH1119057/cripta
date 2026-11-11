"""Fail-closed source identity checks for the isolated CAPFREE100 resume rail."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.research import p90l90_bull2025_capfree100_frozen_resume_v2 as resume


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    root = tmp_path / "isolated"
    path = root / resume.SOURCE_REL
    path.parent.mkdir(parents=True)
    path.write_text("# frozen Git source\n", encoding="utf-8")
    monkeypatch.setattr(resume, "FROZEN_PARENT", tmp_path)
    monkeypatch.setattr(resume.os, "geteuid", lambda: 107, raising=False)
    monkeypatch.setattr(resume.os, "getegid", lambda: 109, raising=False)
    return root


def test_frozen_source_accepts_only_clean_detached_exact_sha(frozen, monkeypatch):
    calls = []

    def fake_git(_root, *args):
        calls.append(args)
        if args == ("rev-parse", "HEAD"):
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n")
        if args == ("symbolic-ref", "--quiet", "HEAD"):
            return SimpleNamespace(returncode=1, stdout="")
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(args)

    monkeypatch.setattr(resume, "git", fake_git)
    resume.frozen_source(frozen, "a" * 40)
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("head", "branch_rc", "dirty", "error"),
    [
        ("b" * 40, 1, "", "HEAD mismatch"),
        ("a" * 40, 0, "", "detached"),
        ("a" * 40, 1, " M scripts/research/unsafe.py", "dirty"),
    ],
)
def test_frozen_source_rejects_drift(
    frozen, monkeypatch, head, branch_rc, dirty, error
):
    def fake_git(_root, *args):
        if args == ("rev-parse", "HEAD"):
            return SimpleNamespace(returncode=0, stdout=head)
        if args == ("symbolic-ref", "--quiet", "HEAD"):
            return SimpleNamespace(returncode=branch_rc, stdout="")
        return SimpleNamespace(returncode=0, stdout=dirty)

    monkeypatch.setattr(resume, "git", fake_git)
    with pytest.raises(RuntimeError, match=error):
        resume.frozen_source(frozen, "a" * 40)


def test_source_rejects_mirror_and_unapproved_actor(frozen, monkeypatch):
    with pytest.raises(PermissionError, match="worktree"):
        resume.frozen_source(resume.FROZEN_PARENT.parent / "source_checkout", "a" * 40)
    monkeypatch.setattr(resume.os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(PermissionError, match="cripta"):
        resume.frozen_source(frozen, "a" * 40)


def test_resume_refuses_existing_durable_launch(frozen, tmp_path, monkeypatch):
    out = tmp_path / "result"
    out.mkdir()
    (out / "resume_launch_v2.json").write_text('{"status": "existing"}', encoding="utf-8")
    monkeypatch.setattr(resume, "OUT", out)
    monkeypatch.setattr(resume, "validate_resume", lambda *_: {"days_done": 35})
    with pytest.raises(FileExistsError, match="reconcile"):
        resume.resume(frozen, "a" * 40, object(), object())
