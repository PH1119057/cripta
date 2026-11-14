from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_single_python_package_root_for_dispatcher() -> None:
    assert not (ROOT / "production").exists()
    assert (ROOT / "src/bybit_workbench/dispatcher_v2").is_dir()
    assert not (ROOT / "src/bybit_workbench/strategy_dispatcher").exists()
    archived = (
        ROOT
        / "archive"
        / "legacy_strategy_dispatcher_source"
        / "bybit_workbench"
        / "strategy_dispatcher"
    )
    assert archived.is_dir()


def test_dispatcher_release_paths_use_single_src_root() -> None:
    unit = (ROOT / "operations/dispatcher_v2/cripta-dispatcher-v2.service").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "operations/infrastructure/install_verified_release.sh").read_text(
        encoding="utf-8"
    )
    assert "runtime/current/src" in unit
    assert "runtime/current/production/src" not in unit
    assert "production/src" not in installer
    assert 'PYTHONPATH="$runtime_release/src"' in installer
