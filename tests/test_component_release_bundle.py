"""Immutable service-candidate packaging tests; no trading/DB/server mutations."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "infrastructure"))
from component_release_bundle import stage_component_bundle  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo_pair(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Bundle Test")
    src = repo / "src" / "bybit_workbench"
    src.mkdir(parents=True)
    (src / "shared.py").write_text("shared unchanged\n", encoding="utf-8")
    (src / "universal_exit").mkdir()
    (src / "universal_exit" / "engine.py").write_text("EXIT_V1\n", encoding="utf-8")
    (src / "universal_entry").mkdir()
    (src / "universal_entry" / "engine.py").write_text("ENTRY_BASELINE\n", encoding="utf-8")
    ops = repo / "operations" / "connectivity"
    ops.mkdir(parents=True)
    (ops / "universal_exit_consumer.py").write_text("EXIT_SCRIPT_BASE\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    base = _git(repo, "rev-parse", "HEAD")
    (src / "universal_exit" / "engine.py").write_text("EXIT_V2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "exit only")
    target = _git(repo, "rev-parse", "HEAD")
    return repo, base, target


def test_exit_bundle_is_content_verified_and_does_not_modify_entry(
    repo_pair: tuple[Path, str, str], tmp_path: Path,
) -> None:
    repo, base, target = repo_pair
    output = tmp_path / "exit-candidate.zip"
    result = stage_component_bundle(
        repo=repo, baseline=base, target=target, component="EXIT", output=output
    )
    assert output.is_file()
    assert result["status"] == "STAGED_ONLY_NOT_DEPLOYABLE"
    assert result["install_authorized"] is False
    assert result["candidate_units"] == ["cripta-universal-exit-consumer.service"]
    with zipfile.ZipFile(output) as bundle:
        assert bundle.read("src/bybit_workbench/universal_exit/engine.py") == b"EXIT_V2\n"
        assert bundle.read("src/bybit_workbench/universal_entry/engine.py") == b"ENTRY_BASELINE\n"
        assert bundle.read(
            "operations/connectivity/universal_exit_consumer.py"
        ) == b"EXIT_SCRIPT_BASE\n"
        manifest = json.loads(bundle.read("component_manifest.json"))
        assert manifest["dependency_environment"] == "NOT_ATTESTED"
        assert manifest["postgresql_compatibility"] == "NOT_ATTESTED"
        for path, expected_hash in manifest["blob_sha256"].items():
            assert hashlib.sha256(bundle.read(path)).hexdigest() == expected_hash
        assert "operations/sql/anything.sql" not in bundle.namelist()
    entry_path = repo / "src" / "bybit_workbench" / "universal_entry" / "engine.py"
    assert entry_path.read_text() == "ENTRY_BASELINE\n"


def test_same_component_candidate_produces_identical_zip_bytes(
    repo_pair: tuple[Path, str, str], tmp_path: Path,
) -> None:
    repo, base, target = repo_pair
    first = tmp_path / "one.zip"
    second = tmp_path / "two.zip"
    for output in (first, second):
        stage_component_bundle(
            repo=repo, baseline=base, target=target, component="EXIT", output=output
        )
    assert first.read_bytes() == second.read_bytes()


def test_no_deployment_or_overwrite_on_unrelated_component(
    repo_pair: tuple[Path, str, str], tmp_path: Path,
) -> None:
    repo, base, target = repo_pair
    output = tmp_path / "none.zip"
    with pytest.raises(ValueError, match="BUNDLE_SCOPE_UNPROVEN"):
        stage_component_bundle(
            repo=repo, baseline=base, target=target, component="ENTRY", output=output
        )
    assert not output.exists()


def test_existing_file_never_overwritten(
    repo_pair: tuple[Path, str, str], tmp_path: Path,
) -> None:
    repo, base, target = repo_pair
    out = tmp_path / "already.zip"
    out.write_bytes(b"PROTECTED")
    with pytest.raises(ValueError, match="BUNDLE_OUTPUT_ALREADY_EXISTS"):
        stage_component_bundle(
            repo=repo, baseline=base, target=target, component="EXIT", output=out
        )
    assert out.read_bytes() == b"PROTECTED"


def test_no_install_authorization_even_for_matching_exact_delta(
    repo_pair: tuple[Path, str, str], tmp_path: Path,
) -> None:
    repo, base, target = repo_pair
    output = tmp_path / "candidate.zip"
    manifest = stage_component_bundle(
        repo=repo, baseline=base, target=target, component="EXIT", output=output
    )
    assert manifest["install_authorized"] is False
    assert "LOADED_COMMIT" not in manifest
    assert "component_manifest.json" in zipfile.ZipFile(output).namelist()
