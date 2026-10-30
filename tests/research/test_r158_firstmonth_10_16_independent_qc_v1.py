"""Issue #158 independent full-period #10/#16 QC contract tests."""
import gzip
import importlib.util
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MATERIALIZER_SOURCE = (
    ROOT
    / "research/microstructure/experiments/r158_firstmonth_10_16_materialize_v1.py"
)
CHECKER_SOURCE = (
    ROOT
    / "research/microstructure/experiments/"
    "r158_firstmonth_10_16_independent_qc_v1.py"
)

mspec = importlib.util.spec_from_file_location(
    "r158_materializer_for_qc", MATERIALIZER_SOURCE
)
assert mspec and mspec.loader
materializer = importlib.util.module_from_spec(mspec)
mspec.loader.exec_module(materializer)

qspec = importlib.util.spec_from_file_location("r158_independent_qc", CHECKER_SOURCE)
assert qspec and qspec.loader
checker = importlib.util.module_from_spec(qspec)
qspec.loader.exec_module(checker)

SYMBOL = "APTUSDT"
START = date(2026, 5, 18)
COMMIT = "b" * 40


def snapshot(day):
    ts = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    return {
        "topic": f"orderbook.200.{SYMBOL}",
        "type": "snapshot",
        "ts": ts,
        "data": {
            "s": SYMBOL,
            "u": 100,
            "seq": 1000,
            "b": [[str(300 - k), "10"] for k in range(200)],
            "a": [[str(310 + k), "10"] for k in range(200)],
        },
    }


def delta(day):
    event = snapshot(day)
    event["type"] = "delta"
    event["ts"] += 100
    event["data"] = {
        "s": SYMBOL,
        "u": 101,
        "seq": 1001,
        "b": [["300", "12"]],
        "a": [["310", "9"]],
    }
    return event


def stage_day(raw_root, day):
    base = raw_root / SYMBOL
    (base / "orderbook").mkdir(parents=True, exist_ok=True)
    (base / "public_trades").mkdir(parents=True, exist_ok=True)
    member = f"{day}_{SYMBOL}_ob200.data"
    with zipfile.ZipFile(base / "orderbook" / f"{member}.zip", "w") as archive:
        archive.writestr(
            member,
            "\n".join(json.dumps(x) for x in (snapshot(day), delta(day))) + "\n",
        )
    start_seconds = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
    )
    with gzip.open(
        base / "public_trades" / f"{SYMBOL}{day}.csv.gz", "wt", newline=""
    ) as stream:
        stream.write("timestamp,symbol,side,size,price,trdMatchID\n")
        stream.write(f"{start_seconds + 1},{SYMBOL},Buy,2,5,t1\n")
        stream.write(f"{start_seconds + 2},{SYMBOL},Sell,3,4,t2\n")


def build_two_day_run(tmp_path):
    raw_root = tmp_path / "raw"
    for offset in range(2):
        stage_day(raw_root, START + timedelta(days=offset))
    runs_parent = tmp_path / "runs"
    runs_parent.mkdir()
    run_root = runs_parent / "r158"
    materializer.materialize_symbol(
        SYMBOL,
        COMMIT,
        raw_root=raw_root,
        output_root=run_root,
        days=2,
        require_actor=False,
    )
    return raw_root, run_root


def test_independent_qc_recomputes_every_row_without_primary_reader_import(
    tmp_path,
):
    raw_root, run_root = build_two_day_run(tmp_path)
    report = checker.check_symbol(
        SYMBOL,
        raw_root=raw_root,
        run_root=run_root,
        days=2,
        require_actor=False,
    )
    assert report["status"] == (
        "FULL_PERIOD_INDEPENDENT_SOURCE_QC_PASS_NOT_RESEARCH_READY"
    )
    assert report["checked_minute_rows_per_indicator"] == 2880
    assert report["indicator_ids"] == [10, 16]
    assert len(report["source_days"]) == 2
    assert report["research_ready"] is False
    assert report["verified_available_after"] is False
    saved = json.loads(
        (run_root / SYMBOL / "independent_qc_report.json").read_text()
    )
    assert saved == report

    source_text = CHECKER_SOURCE.read_text()
    assert "import r158_ofi_top5_exact_union_v1" not in source_text
    assert "import r158_absorption_components_v1" not in source_text

    with pytest.raises(FileExistsError, match="IMMUTABLE_QC_REPORT"):
        checker.check_symbol(
            SYMBOL,
            raw_root=raw_root,
            run_root=run_root,
            days=2,
            require_actor=False,
        )


def test_qc_rejects_materialized_output_hash_drift(tmp_path):
    raw_root, run_root = build_two_day_run(tmp_path)
    path = run_root / SYMBOL / "indicator_10_ofi_1m.jsonl.gz"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="OFI_OUTPUT_HASH_DRIFT"):
        checker.check_symbol(
            SYMBOL,
            raw_root=raw_root,
            run_root=run_root,
            days=2,
            require_actor=False,
        )
