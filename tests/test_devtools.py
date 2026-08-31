from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from operations.devtools.cli import baseline_check, classify, load_patch


def make_patch(path: Path, body: bytes = b"new", digest: str | None = None) -> None:
    manifest = {
        "id": "fixture",
        "files": [
            {"path": "docs/value.txt", "sha256": digest or hashlib.sha256(body).hexdigest()}
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("MANIFEST.json", json.dumps(manifest))
        archive.writestr("payload/docs/value.txt", body)


def test_classification_marks_trading_and_operations() -> None:
    result = classify(["src/x/risk.py", "operations/devtools/cli.py"])
    assert result["RISK"] == ["src/x/risk.py"]
    assert result["OPERATIONS"] == ["operations/devtools/cli.py"]


def test_patch_rejects_bad_sha(tmp_path: Path) -> None:
    patch = tmp_path / "bad.zip"
    make_patch(patch, digest="0" * 64)
    with pytest.raises(ValueError, match="bad SHA256"):
        load_patch(patch)


def test_patch_rejects_unsafe_path(tmp_path: Path) -> None:
    patch = tmp_path / "unsafe.zip"
    manifest = {"files": [{"path": "../x", "sha256": hashlib.sha256(b"x").hexdigest()}]}
    with zipfile.ZipFile(patch, "w") as archive:
        archive.writestr("MANIFEST.json", json.dumps(manifest))
        archive.writestr("payload/../x", b"x")
    with pytest.raises(ValueError, match="unsafe archive path"):
        load_patch(patch)


def test_wrong_baseline_is_blocked(tmp_path: Path) -> None:
    (tmp_path / "PROJECT_GIT_HEAD.txt").write_text("current\n", encoding="utf-8")
    errors = baseline_check(tmp_path, {"baseline": {"policy": "EXACT_COMMIT", "commit": "other"},
                                       "files": []})
    assert any("wrong baseline" in value for value in errors)


def test_file_hash_contract_passes_without_git(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    digest = hashlib.sha256(b"old").hexdigest()
    manifest = {"baseline": {"policy": "FILE_HASH_CONTRACT"},
                "files": [{"path": "a.txt", "before_sha256": digest}]}
    assert baseline_check(tmp_path, manifest) == []


def test_powershell_is_only_transport_wrapper() -> None:
    text = Path("scripts/patch/INSTALL_CRIPTA_PATCH.ps1").read_text(encoding="utf-8")
    assert "cripta-patch $Action" in text
    assert "Get-FileHash" in text
    assert "Expand-Archive" not in text
