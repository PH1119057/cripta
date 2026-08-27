from pathlib import Path

import pytest

from bybit_workbench.research.universal_entry_pipeline import PipelineConfig


def test_pipeline_config_accepts_arbitrary_symbols(tmp_path: Path) -> None:
    config = PipelineConfig(
        raw_root=tmp_path / "raw",
        work_root=tmp_path / "work",
        symbols=("TRXUSDT", "INJUSDT"),
        workers=2,
        max_days=10,
    )
    assert config.symbols == ("TRXUSDT", "INJUSDT")


def test_pipeline_config_rejects_zero_days(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_days"):
        PipelineConfig(tmp_path, tmp_path / "work", ("TRXUSDT",), 1, 0)
