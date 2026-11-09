"""Pure P90L90 bull-regime 2025 acquisition contract and real gzip CSV QC tests."""
from __future__ import annotations

import csv
import gzip
import importlib.util
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

RUNNER = (
    Path(__file__).resolve().parents[2]
    / "scripts/research/p90l90_bull2025_acquire_v1.py"
)


def module():
    spec = importlib.util.spec_from_file_location("bull2025_acquire", RUNNER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # The production runner is Linux-only; pure schema/contract tests also run
    # on Windows CI, where the Linux kernel locking module is not installed.
    dummy_fcntl = types.ModuleType("fcntl")
    with patch.dict(sys.modules, {"fcntl": dummy_fcntl}):
        spec.loader.exec_module(mod)
    return mod


def test_pinned_window_symbols_source_and_scope() -> None:
    src = RUNNER.read_text(encoding="utf8")
    compile(src, str(RUNNER), "exec")
    m = module()
    assert m.EVAL_START == "2025-04-01T00:00:00Z"
    assert m.EVAL_END == "2025-10-01T00:00:00Z"
    assert m.FIRST.isoformat() == "2025-03-22"
    assert m.LAST.isoformat() == "2025-09-30"
    assert len(list(m.days(m.FIRST, m.LAST))) == 193
    assert m.SYMBOLS == ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
    assert m.ROOT.as_posix() == (
        "/data/cripta/research/runs/p90l90_bull_2025_holdout_v1"
    )
    assert m.RESERVE_BYTES >= 15 * 1024**3
    assert "assert os.geteuid() == 0" not in src
    assert "if os.geteuid() != 107:" in src
    assert 'fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)' in src
    assert 'm["acquisition_stage"] = "PILOT_QC_PASS" if pilot else "FULL_QC_PASS"' in src
    assert "source_snapshot_sha256" in src
    assert "INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY" in src
    assert "P90L90_BULLREGIME_2025_V1" in src


def write_gzip_day(path: Path, rows: list[dict[str, str]]) -> None:
    with gzip.open(path, "wt", newline="", encoding="utf8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "symbol", "price", "tickDirection", "trdMatchID"]
        )
        writer.writeheader()
        writer.writerows(rows)


def test_native_source_day_good_and_bad(tmp_path: Path) -> None:
    m = module()
    day = m.PILOT_DAY
    epoch = datetime(2025, 3, 22, tzinfo=UTC).timestamp()
    path = tmp_path / "LTCUSDT2025-03-22.csv.gz"
    good = [
        {"timestamp": str(epoch + 1.1), "symbol": "LTCUSDT", "price": "85.5",
         "tickDirection": "PlusTick", "trdMatchID": "eventA"},
        {"timestamp": str(epoch + 1.1), "symbol": "LTCUSDT", "price": "85.55",
         "tickDirection": "ZeroPlusTick", "trdMatchID": "eventB"},
        {"timestamp": str(epoch + 15), "symbol": "LTCUSDT", "price": "85.3",
         "tickDirection": "MinusTick", "trdMatchID": "eventC"},
    ]
    write_gzip_day(path, good)
    checked = m.validate_archive("LTCUSDT", day, path)
    assert checked["rows"] == 3
    assert checked["size_bytes"] == path.stat().st_size
    assert len(checked["sha256"]) == 64

    write_gzip_day(path, [good[1], good[0]])
    with pytest.raises(ValueError, match="empty|off|out-of-order"):
        m.validate_archive("DOTUSDT", day, path)

    bad = [{**good[0], "timestamp": str(epoch - 1)}]
    write_gzip_day(path, bad)
    with pytest.raises(ValueError, match="off-symbol/off-day"):
        m.validate_archive("LTCUSDT", day, path)

    bad = [good[2], good[0]]
    write_gzip_day(path, bad)
    with pytest.raises(ValueError, match="out-of-order"):
        m.validate_archive("LTCUSDT", day, path)

    write_gzip_day(path, [])
    with pytest.raises(ValueError, match="empty"):
        m.validate_archive("LTCUSDT", day, path)
