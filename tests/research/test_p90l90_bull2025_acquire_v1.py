"""Pure P90L90 bull-regime 2025 acquisition contract and real gzip CSV QC tests."""
from __future__ import annotations

import csv
import gzip
import hashlib
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

    write_gzip_day(path, [good[0], {**good[1], "trdMatchID": "eventA"}])
    with pytest.raises(ValueError, match="duplicate native execution ID"):
        m.validate_archive("LTCUSDT", day, path)

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


def test_regression_child_multimegabyte_output_no_pipe_deadlock(tmp_path: Path) -> None:
    m = module()
    log = tmp_path / "large_download.log"
    cmd = [
        sys.executable, "-c",
        "import sys; sys.stdout.write('A'*1200000); sys.stdout.flush(); "
        "sys.stderr.write('B'*1200000); sys.stderr.flush()",
    ]
    code = m.run_logged_child(cmd, log, lambda _pid: None, interval_seconds=0.02)
    assert code == 0
    assert log.stat().st_size == 2400000
    with log.open("rb") as stream:
        assert stream.read(2) == b"AA"
        stream.seek(-2, 2)
        assert stream.read(2) == b"BB"
    # Never overwrite a completed previous source/attempt log.
    with pytest.raises(FileExistsError):
        m.run_logged_child(cmd, log, lambda _pid: None, interval_seconds=0.02)


def test_repair_provenance_is_distinct_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = module()
    source_dir = tmp_path / "repository"
    root = tmp_path / "data"
    source_dir.mkdir()
    root.mkdir()
    original = b"original research runner (historical snapshot)"
    repaired = b"safe logged child, no undrained pipe"
    file = source_dir / "runner.py"
    file.write_bytes(repaired)
    monkeypatch.setattr(m, "SOURCE", source_dir)
    monkeypatch.setattr(m, "ROOT", root)
    monkeypatch.setattr(m, "FILES", ("runner.py",))
    sha = hashlib.sha256(repaired).hexdigest()
    old_hash = hashlib.sha256(original).hexdigest()
    base = "a" * 40
    head = "b" * 40
    manifest = {
        "source_head": base, "source_snapshot_sha256": {"runner.py": old_hash}
    }
    m.snapshot_resume_source(head, manifest)
    saved = root / "recovery_sources" / f"{head}_p90l90_bull2025_acquire_v1.py"
    record = root / "recovery_sources" / f"{head}_provenance.json"
    assert saved.read_bytes() == repaired
    assert old_hash != sha
    assert old_hash in record.read_text()
    # Retrying the exact same evidence is read-only/idempotent.
    m.snapshot_resume_source(head, manifest)
    saved.write_bytes(b"corrupted source")
    with pytest.raises(RuntimeError, match="drifted"):
        m.snapshot_resume_source(head, manifest)
