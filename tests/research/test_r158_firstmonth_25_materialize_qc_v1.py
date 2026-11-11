"""Issue #158 #25 full first-month materialization + independent QC tests."""
import csv
import gzip
import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAT = (
    ROOT
    / "research/microstructure/experiments/"
    "r158_firstmonth_25_materialize_v1.py"
)
QC = (
    ROOT
    / "research/microstructure/experiments/"
    "r158_firstmonth_25_independent_qc_v1.py"
)

mspec = importlib.util.spec_from_file_location("r158_25_materializer", MAT)
assert mspec and mspec.loader
materializer = importlib.util.module_from_spec(mspec)
mspec.loader.exec_module(materializer)

qspec = importlib.util.spec_from_file_location("r158_25_qc", QC)
assert qspec and qspec.loader
checker = importlib.util.module_from_spec(qspec)
qspec.loader.exec_module(checker)

SYMBOL = "APTUSDT"
START = date(2026, 5, 18)
COMMIT = "a" * 40


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_spot(path, day):
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "timestamp", "price", "volume", "side", "rpi"])
        writer.writerow(["s1", str(ts + 1000), "10", "2", "buy", "0"])
        writer.writerow(["s2", str(ts + 2000), "12", "1", "sell", "0"])


def write_perp(path, day, symbol):
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        fields = [
            "timestamp",
            "symbol",
            "side",
            "size",
            "price",
            "tickDirection",
            "trdMatchID",
            "grossValue",
            "homeNotional",
            "foreignNotional",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": str(ts + 1),
                "symbol": symbol,
                "side": "Buy",
                "size": "3",
                "price": "11",
                "tickDirection": "PlusTick",
                "trdMatchID": "p1",
                "grossValue": "0",
                "homeNotional": "3",
                "foreignNotional": "33",
            }
        )


def stage_sources(tmp_path):
    spot_root = tmp_path / "spot"
    perp_root = tmp_path / "perp"
    records = []
    for symbol in materializer.COHORT:
        for offset in range(30):
            day = START + timedelta(days=offset)
            if symbol == SYMBOL:
                spot = spot_root / symbol / f"{symbol}_{day}.csv.gz"
                write_spot(spot, day)
                write_perp(
                    perp_root
                    / symbol
                    / "public_trades"
                    / f"{symbol}{day}.csv.gz",
                    day,
                    symbol,
                )
                records.append(
                    {
                        "symbol": symbol,
                        "date_utc": day.isoformat(),
                        "bytes": spot.stat().st_size,
                        "sha256": sha(spot),
                        "origin": "SYNTHETIC_TEST_ORIGINAL",
                    }
                )
            else:
                records.append(
                    {
                        "symbol": symbol,
                        "date_utc": day.isoformat(),
                        "bytes": 1,
                        "sha256": "0" * 64,
                        "origin": "UNUSED_TEST_PLACEHOLDER",
                    }
                )
    manifest = {
        "status": "COMPLETE_IMMUTABLE_RESEARCH_SOURCE_CORPUS",
        "expected_files": 150,
        "files": 150,
        "file_records": records,
    }
    (spot_root / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return spot_root, perp_root


def test_full_30d_materialization_and_independent_qc(tmp_path):
    spot_root, perp_root = stage_sources(tmp_path)
    parent = tmp_path / "runs"
    parent.mkdir()
    output = parent / "r158-25"

    final = materializer.materialize_symbol(
        SYMBOL,
        COMMIT,
        spot_root=spot_root,
        perp_root=perp_root,
        output_root=output,
        require_actor=False,
    )
    assert final["rows"] == 43_200
    assert final["observed_minutes"] == 30
    assert final["no_data_minutes"] == 43_170
    assert final["research_ready"] is False

    report = checker.check_symbol(
        SYMBOL,
        spot_root=spot_root,
        perp_root=perp_root,
        run_root=output,
        require_actor=False,
    )
    assert report["status"] == (
        "SPOT_PERP_25_FIRSTMONTH_INDEPENDENT_FULLROW_QC_PASS_NOT_RESEARCH_READY"
    )
    assert report["checked_rows"] == 43_200
    assert report["observed_minutes"] == 30
    assert len(report["source_days"]) == 30
    assert report["research_ready"] is False


def test_checker_rejects_materialized_output_hash_drift(tmp_path):
    spot_root, perp_root = stage_sources(tmp_path)
    parent = tmp_path / "runs"
    parent.mkdir()
    output = parent / "r158-25"
    materializer.materialize_symbol(
        SYMBOL,
        COMMIT,
        spot_root=spot_root,
        perp_root=perp_root,
        output_root=output,
        require_actor=False,
    )
    artifact = output / SYMBOL / "divergence_1m.jsonl.gz"
    artifact.write_bytes(artifact.read_bytes() + b"x")
    with pytest.raises(ValueError, match="QC_MATERIALIZED_OUTPUT_HASH_DRIFT"):
        checker.check_symbol(
            SYMBOL,
            spot_root=spot_root,
            perp_root=perp_root,
            run_root=output,
            require_actor=False,
        )
