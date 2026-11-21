"""Management controller independent source/live attestation (#871).

All OS/Git operations are inert, mocked and point at pytest tmp paths.
The production checker must never infer authorization solely from file names.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "src/bybit_workbench/source_live_identity.py"
)
spec = importlib.util.spec_from_file_location(
    "source_live_identity_871",
    MODULE,
)
identity = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = identity
spec.loader.exec_module(identity)

LOADED = "a" * 40
TARGET = "b" * 40
LIVE_BYTES = b"#!/usr/bin/python3\n# verified by protected git+CI\n"
CONTROLLER = identity.CONTROLLER_SOURCE_PATH


def test_existing_documents_and_dashboard_classification_is_unchanged():
    assert identity.classify_delta_paths(
        ["docs/INDEX.md", "tests/test_a.py", "operations/dashboard/index.html"],
        dashboard_delta_verified=True,
    ) == (True, None)
    assert identity.classify_delta_paths(
        ["operations/dashboard/index.html"],
        dashboard_delta_verified=False,
    ) == (False, "DASHBOARD_UI_DELTA_NOT_VERIFIED")


def test_controller_not_whitelisted_without_live_exact_attestation():
    assert identity.classify_delta_paths(
        [CONTROLLER], dashboard_delta_verified=True,
    ) == (False, "MANAGEMENT_CONTROLLER_DELTA_NOT_VERIFIED")
    assert identity.classify_delta_paths(
        [CONTROLLER],
        dashboard_delta_verified=False,
        controller_delta_verified=True,
    ) == (True, None)


@pytest.mark.parametrize("path", [
    "operations/infrastructure/deploy_release_controller.py",
    "operations/infrastructure/cripta-release-controller.service",
    "src/bybit_workbench/source_live_identity.py",
    "operations/dashboard/app.py.unsigned",
    "operations/systemd/cripta-universal-entry-consumer.service",
    "db/migrations/20261120.sql",
    "tests/conftest.py",
])
def test_never_infer_approval_for_neighboring_code(path):
    assert identity.classify_delta_paths(
        [CONTROLLER, path],
        dashboard_delta_verified=True,
        controller_delta_verified=True,
    ) == (False, "TRADING_OR_UNKNOWN_SOURCE_DELTA")


def _setup_proof(monkeypatch, tmp_path):
    live = tmp_path / "cripta-release-controller"
    live.write_bytes(LIVE_BYTES)
    release = tmp_path / "release"
    release.mkdir()
    proof = release / "MANAGEMENT_CONTROLLER_DEPLOY.json"
    digest = hashlib.sha256(LIVE_BYTES).hexdigest()
    record = {
        "schema_version": 1,
        "management_controller_commit": TARGET,
        "source_path": CONTROLLER,
        "installed_path": str(live),
        "installed_sha256": digest,
    }
    proof.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(identity, "CONTROLLER_LIVE_PATH", live)
    monkeypatch.setattr(identity, "_git_is_ancestor", lambda *_: True)
    git_calls = []

    def fake_git(_root, *args):
        git_calls.append(args)
        if args == (
            "diff", "--name-only", "--no-renames",
            TARGET, TARGET, "--", CONTROLLER,
        ):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(identity, "_git", fake_git)
    monkeypatch.setattr(
        identity.subprocess,
        "check_output",
        lambda *_a, **_kw: LIVE_BYTES,
    )
    original_lstat = Path.lstat

    def fake_lstat(path):
        if path == live:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0)
        if path == proof:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    return live, release, proof, record, git_calls


def _verified(live, release):
    return identity._controller_deploy_evidence(
        loaded_commit=LOADED,
        source_head=TARGET,
        source=Path("/source/readonly"),
        release_state=release,
        controller_live=live,
    )


def test_exact_verified_live_controller_proof_passes(monkeypatch, tmp_path):
    live, release, _, _, calls = _setup_proof(monkeypatch, tmp_path)
    assert _verified(live, release) is True
    assert calls == [
        (
            "diff", "--name-only", "--no-renames",
            TARGET, TARGET, "--", CONTROLLER,
        ),
    ]


def test_missing_proof_cannot_pass(monkeypatch, tmp_path):
    live, release, proof, _, _ = _setup_proof(monkeypatch, tmp_path)
    proof.unlink()
    assert _verified(live, release) is False


@pytest.mark.parametrize("bad", [
    {"schema_version": 2},
    {"management_controller_commit": "not-a-sha"},
    {"installed_sha256": "0" * 64},
    {"installed_sha256": "INVALID"},
    {"source_path": "src/bybit_workbench/entry.py"},
    {"installed_path": "/tmp/mutable-controller"},
])
def test_wrong_marker_field_blocks(monkeypatch, tmp_path, bad):
    live, release, proof, record, _ = _setup_proof(monkeypatch, tmp_path)
    record.update(bad)
    proof.write_text(json.dumps(record), encoding="utf-8")
    assert _verified(live, release) is False


def test_installed_bytes_changed_after_attestation_blocks(monkeypatch, tmp_path):
    live, release, _, _, _ = _setup_proof(monkeypatch, tmp_path)
    live.write_bytes(b"tampered")
    assert _verified(live, release) is False


def test_only_exact_target_path_eligible(monkeypatch, tmp_path):
    live, release, _, _, _ = _setup_proof(monkeypatch, tmp_path)
    other = tmp_path / "other"
    other.write_bytes(LIVE_BYTES)
    assert identity._controller_deploy_evidence(
        loaded_commit=LOADED,
        source_head=TARGET,
        source=Path("/source/readonly"),
        release_state=release,
        controller_live=other,
    ) is False


def test_post_deploy_source_change_requires_new_controller_install(
    monkeypatch, tmp_path,
):
    live, release, _, _, _ = _setup_proof(monkeypatch, tmp_path)
    monkeypatch.setattr(
        identity, "_git", lambda *_a: CONTROLLER,
    )
    assert _verified(live, release) is False


def test_untrusted_marker_or_executable_owner_blocks(monkeypatch, tmp_path):
    live, release, proof, _, _ = _setup_proof(monkeypatch, tmp_path)
    original_lstat = Path.lstat

    def untrusted(path):
        if path == proof:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o666, st_uid=0)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", untrusted)
    assert _verified(live, release) is False


def test_git_original_bytes_must_match_installed_source(monkeypatch, tmp_path):
    live, release, _, _, _ = _setup_proof(monkeypatch, tmp_path)
    monkeypatch.setattr(
        identity.subprocess, "check_output", lambda *_a, **_kw: b"other\n",
    )
    assert _verified(live, release) is False


def test_end_to_end_source_live_identity_accepts_only_attested_controller(
    monkeypatch, tmp_path,
):
    live, release, proof, _, _ = _setup_proof(monkeypatch, tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    controller_path = identity.CONTROLLER_SOURCE_PATH

    def fake_git(_root, *args):
        if args == ("rev-parse", "HEAD"):
            return TARGET
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return TARGET + "\trefs/heads/main"
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return ""
        if args == (
            "diff", "--name-only", f"{LOADED}..{TARGET}"
        ):
            return controller_path + "\ntests/test_management_controller_identity_871.py"
        if args == (
            "diff", "--name-only", "--no-renames",
            TARGET, TARGET, "--", controller_path,
        ):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(identity, "_git", fake_git)
    actual = identity.inspect_source_live_identity(
        loaded_commit=LOADED,
        source=source,
        release_state=release,
        controller_live=live,
    )
    assert actual.ok is True
    assert actual.blocker is None
    assert controller_path in actual.changed_paths

    proof.unlink()
    blocked = identity.inspect_source_live_identity(
        loaded_commit=LOADED,
        source=source,
        release_state=release,
        controller_live=live,
    )
    assert blocked.ok is False
    assert blocked.blocker == "MANAGEMENT_CONTROLLER_DELTA_NOT_VERIFIED"
