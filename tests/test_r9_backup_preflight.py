from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "operations/infrastructure/r9_backup_preflight.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")

spec = importlib.util.spec_from_file_location("r9_backup_preflight", SOURCE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _backup(tmp_path: Path, size: int = 100) -> Path:
    name = (
        "20261017_010203_"
        + "a" * 40
        + "_to_"
        + "b" * 40
    )
    root = tmp_path / "release_backups"
    item = root / name
    item.mkdir(parents=True)
    (item / "cripta_before.dump").write_bytes(b"x" * size)
    return root


def test_backup_preflight_is_read_only() -> None:
    for forbidden in (
        "unlink(",
        "rmdir(",
        "mkdir(",
        "write_bytes(",
        "write_text(",
        "shutil.rmtree",
        "subprocess",
    ):
        assert forbidden not in SOURCE


def test_backup_preflight_fails_when_free_space_is_below_reference_dump(
    tmp_path: Path,
) -> None:
    root = _backup(tmp_path, size=100)
    module._free_bytes = lambda _path: 99
    result = module.inspect_backup_headroom(root)
    assert result.status == "FAIL"
    assert result.reference_dump_bytes == 100
    assert "INSUFFICIENT_HEADROOM" in result.detail


def test_backup_preflight_passes_only_minimum_reference_comparison(
    tmp_path: Path,
) -> None:
    root = _backup(tmp_path, size=100)
    module._free_bytes = lambda _path: 101
    result = module.inspect_backup_headroom(root)
    assert result.status == "PASS"
    assert result.free_bytes == 101
    assert "MINIMUM_HEADROOM" in result.detail


def test_backup_preflight_fails_without_reference_backup(tmp_path: Path) -> None:
    root = tmp_path / "release_backups"
    root.mkdir()
    result = module.inspect_backup_headroom(root)
    assert result.status == "FAIL"
    assert result.detail == "NO_REFERENCE_DEPLOY_BACKUP"
