"""Issue #158 first-month #13 materializer + independent QC tests."""
import importlib.util
import json
import sys
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MATERIALIZER_SOURCE = (
    ROOT
    / "research/microstructure/experiments/"
    "r158_firstmonth_13_materialize_v1.py"
)
CHECKER_SOURCE = (
    ROOT
    / "research/microstructure/experiments/"
    "r158_firstmonth_13_independent_qc_v1.py"
)

mspec = importlib.util.spec_from_file_location(
    "r158_depth13_materializer_test", MATERIALIZER_SOURCE
)
assert mspec and mspec.loader
materializer = importlib.util.module_from_spec(mspec)
sys.modules[mspec.name] = materializer
mspec.loader.exec_module(materializer)

qspec = importlib.util.spec_from_file_location(
    "r158_depth13_qc_test", CHECKER_SOURCE
)
assert qspec and qspec.loader
checker = importlib.util.module_from_spec(qspec)
sys.modules[qspec.name] = checker
qspec.loader.exec_module(checker)

SYMBOL = "APTUSDT"
START = date(2026, 5, 18)
COMMIT = "f" * 40


def snapshot(symbol, ts, u=100, seq=1000):
    return {
        "topic": f"orderbook.200.{symbol}",
        "type": "snapshot",
        "ts": ts,
        "data": {
            "s": symbol,
            "u": u,
            "seq": seq,
            "b": [[str(300 - k), "10"] for k in range(200)],
            "a": [[str(310 + k), "10"] for k in range(200)],
        },
    }


def delta(symbol, ts, u, seq, *, bid=None, ask=None):
    return {
        "topic": f"orderbook.200.{symbol}",
        "type": "delta",
        "ts": ts,
        "data": {
            "s": symbol,
            "u": u,
            "seq": seq,
            "b": bid or [],
            "a": ask or [],
        },
    }


def stage_day(raw_root, symbol, day):
    base = raw_root / symbol / "orderbook"
    base.mkdir(parents=True, exist_ok=True)
    ts = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
    )
    records = [
        snapshot(symbol, ts),
        delta(symbol, ts + 1000, 101, 1001, bid=[["300", "8"]]),
        delta(symbol, ts + 2000, 102, 1002, bid=[["300", "10"]]),
        delta(
            symbol,
            ts + 86400000 + 132,
            103,
            1003,
            bid=[["300", "9"]],
        ),
    ]
    path = base / f"{day}_{symbol}_ob200.data.zip"
    member = f"{day}_{symbol}_ob200.data"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(member, "\n".join(json.dumps(x) for x in records) + "\n")
    return path


def stage_month(raw_root, symbol=SYMBOL):
    for offset in range(30):
        stage_day(raw_root, symbol, START + timedelta(days=offset))


def test_full_30d_materialization_then_independent_fullrow_qc(tmp_path):
    raw = tmp_path / "raw"
    stage_month(raw)
    runs = tmp_path / "runs"
    runs.mkdir()
    output = runs / "r158-depth13"

    final = materializer.materialize_symbol(
        SYMBOL,
        COMMIT,
        raw_root=raw,
        output_root=output,
        require_actor=False,
    )

    assert final["status"] == (
        "DEPTH13_FIRSTMONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
    )
    assert final["days"] == 30
    assert final["episodes"] == 30
    assert final["rows"] == 90
    assert final["outcome_counts"]["R_15s"] == 30
    assert final["outcome_counts"]["R_60s"] == 30
    assert final["outcome_counts"]["R_300s"] == 30
    assert final["research_ready"] is False

    report = checker.check_symbol(
        SYMBOL,
        raw_root=raw,
        run_root=output,
        require_actor=False,
    )
    assert report["status"] == (
        "DEPTH13_FIRSTMONTH_INDEPENDENT_FULLROW_QC_PASS_NOT_RESEARCH_READY"
    )
    assert report["checked_days"] == 30
    assert report["checked_rows"] == 90
    assert all(
        item["source_suffix_records"] == 1
        for item in report["source_days"]
    )
    assert report["research_ready"] is False

    target = output / SYMBOL
    assert (target / "run_start.json").is_file()
    assert (target / "run_final.json").is_file()
    assert (target / "input_provenance.json").is_file()
    assert (target / "independent_qc_report.json").is_file()
    assert (
        target / "source_snapshot/r158_depth_recovery_exact_price_v1.py"
    ).is_file()

    with pytest.raises(FileExistsError, match="IMMUTABLE_SYMBOL_OUTPUT"):
        materializer.materialize_symbol(
            SYMBOL,
            COMMIT,
            raw_root=raw,
            output_root=output,
            require_actor=False,
        )


def test_missing_day_fails_before_symbol_output_creation(tmp_path):
    raw = tmp_path / "raw"
    for offset in range(29):
        stage_day(raw, SYMBOL, START + timedelta(days=offset))
    runs = tmp_path / "runs"
    runs.mkdir()
    output = runs / "r158-depth13"

    with pytest.raises(FileNotFoundError, match="ORIGINAL_OB200_UNREADABLE"):
        materializer.materialize_symbol(
            SYMBOL,
            COMMIT,
            raw_root=raw,
            output_root=output,
            require_actor=False,
        )
    assert not (output / SYMBOL).exists()


def test_checker_rejects_materialized_output_hash_drift(tmp_path):
    raw = tmp_path / "raw"
    stage_month(raw)
    runs = tmp_path / "runs"
    runs.mkdir()
    output = runs / "r158-depth13"
    materializer.materialize_symbol(
        SYMBOL,
        COMMIT,
        raw_root=raw,
        output_root=output,
        require_actor=False,
    )
    artifact = output / SYMBOL / "episode_horizons.jsonl.gz"
    artifact.write_bytes(artifact.read_bytes() + b"x")

    with pytest.raises(ValueError, match="MATERIALIZED_OUTPUT_HASH_DRIFT"):
        checker.check_symbol(
            SYMBOL,
            raw_root=raw,
            run_root=output,
            require_actor=False,
        )
