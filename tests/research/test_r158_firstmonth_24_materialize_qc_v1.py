"""Issue #158 #24 full first-month liquidation materialization/QC tests."""
import csv
import gzip
import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAT = (
    ROOT
    / "research/microstructure/experiments/"
    "r158_firstmonth_24_materialize_v1.py"
)
QC = (
    ROOT
    / "research/microstructure/experiments/"
    "r158_firstmonth_24_independent_qc_v1.py"
)

mspec = importlib.util.spec_from_file_location("r158_24_materializer", MAT)
assert mspec and mspec.loader
materializer = importlib.util.module_from_spec(mspec)
mspec.loader.exec_module(materializer)

qspec = importlib.util.spec_from_file_location("r158_24_qc", QC)
assert qspec and qspec.loader
checker = importlib.util.module_from_spec(qspec)
qspec.loader.exec_module(checker)

SYMBOL = "APTUSDT"
DAY = date(2026, 5, 18)
HOUR = "00"
COMMIT = "b" * 40


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_event():
    start = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp())
    received_ns = (start + 10) * 1_000_000_000 + 123_000_000
    trade_ms = (start + 9) * 1000 + 456
    return {
        "received_time": received_ns,
        "event_time": trade_ms * 1_000_000,
        "symbol": SYMBOL,
        "side": "BUY",
        "order_type": "MARKET",
        "time_in_force": "IOC",
        "quantity": "2.5",
        "price": "10",
        "average_price": "10",
        "order_status": "FILLED",
        "last_filled_quantity": "2.5",
        "filled_quantity": "2.5",
        "trade_time": trade_ms,
    }


def stage(tmp_path):
    vendor = tmp_path / "vendor"
    raw = vendor / "raw" / str(DAY) / HOUR / f"{SYMBOL}_liquidations.parquet.zst"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"synthetic-original-object")
    item = {
        "key": f"bybit/{DAY}/{HOUR}/{SYMBOL}_liquidations.parquet.zst",
        "symbol": SYMBOL,
        "date": str(DAY),
        "hour": HOUR,
        "bytes": raw.stat().st_size,
        "sha256": sha(raw),
    }
    (vendor / "OBJECTS.jsonl").write_text(
        json.dumps(item) + "\n", encoding="utf-8"
    )

    normalized = tmp_path / "normalized"
    normalized.mkdir()
    row = raw_event()
    source_path = f"raw/{DAY}/{HOUR}/{SYMBOL}_liquidations.parquet.zst"
    with gzip.open(
        normalized / f"{SYMBOL}.csv.gz",
        "wt",
        encoding="utf-8",
        newline="",
    ) as stream:
        fields = [
            "observed_timestamp_us",
            "trade_timestamp_us",
            "symbol",
            "liquidated_position_side",
            "price",
            "size",
            "order_status",
            "source_path",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "observed_timestamp_us": str(row["received_time"] // 1000),
                "trade_timestamp_us": str(row["trade_time"] * 1000),
                "symbol": SYMBOL,
                "liquidated_position_side": "SHORT",
                "price": "10",
                "size": "2.5",
                "order_status": "FILLED",
                "source_path": source_path,
            }
        )

    parity = tmp_path / "parity"
    parity.mkdir()
    parity_report = {
        "status": "ORIGINAL_PARQUET_NORMALIZED_FULL_ROW_PARITY_PASS",
        "objects": 1,
        "raw_rows": 1,
        "normalized_rows": 1,
        "receipt_manifest_sha256": sha(vendor / "OBJECTS.jsonl"),
        "by_symbol": {SYMBOL: {"objects": 1, "rows": 1}},
    }
    (parity / "parquet_normalized_parity_report.json").write_text(
        json.dumps(parity_report), encoding="utf-8"
    )
    return vendor, normalized, parity, raw, row


def test_full_grid_materialization_then_original_parquet_qc(tmp_path):
    vendor, normalized, parity, _, row = stage(tmp_path)
    parent = tmp_path / "runs"
    parent.mkdir()
    output = parent / "r158-24"

    final = materializer.materialize_symbol(
        SYMBOL,
        COMMIT,
        vendor_root=vendor,
        normalized_root=normalized,
        parity_root=parity,
        output_root=output,
        require_actor=False,
        expected_total_objects=1,
        expected_total_rows=1,
    )
    assert final["rows"] == 54_720
    assert final["original_object_hours"] == 1
    assert final["missing_object_hours"] == 719
    assert final["normalized_rows"] == 1

    report = checker.check_symbol(
        SYMBOL,
        vendor_root=vendor,
        run_root=output,
        decoder=lambda _: ([row], set(row)),
        require_actor=False,
    )
    assert report["status"] == (
        "LIQUIDATION_24_FIRSTMONTH_INDEPENDENT_ORIGINAL_PARQUET_QC_PASS_NOT_RESEARCH_READY"
    )
    assert report["checked_rows"] == 54_720
    assert report["original_object_hours"] == 1
    assert report["missing_object_hours"] == 719
    assert report["raw_parquet_rows"] == 1
    assert report["research_ready"] is False

    with gzip.open(
        output / SYMBOL / "liquidation_windows.jsonl.gz",
        "rt",
        encoding="utf-8",
    ) as stream:
        rows = [json.loads(next(stream)) for _ in range(2)]
    assert rows[0]["quality"] == "SOURCE_OBJECT_PRESENT"
    assert rows[0]["count"] == 1
    assert rows[0]["liquidated_short_count"] == 1
    assert rows[0]["published_bankruptcy_price_x_qty_usdt"] == "25.0"
    assert rows[1]["quality"] == "SOURCE_OBJECT_PRESENT"
    assert rows[1]["count"] == 0


def test_checker_rejects_materialized_hash_drift(tmp_path):
    vendor, normalized, parity, _, row = stage(tmp_path)
    parent = tmp_path / "runs"
    parent.mkdir()
    output = parent / "r158-24"
    materializer.materialize_symbol(
        SYMBOL,
        COMMIT,
        vendor_root=vendor,
        normalized_root=normalized,
        parity_root=parity,
        output_root=output,
        require_actor=False,
        expected_total_objects=1,
        expected_total_rows=1,
    )
    artifact = output / SYMBOL / "liquidation_windows.jsonl.gz"
    artifact.write_bytes(artifact.read_bytes() + b"x")
    with pytest.raises(ValueError, match="QC_MATERIALIZED_OUTPUT_HASH_DRIFT"):
        checker.check_symbol(
            SYMBOL,
            vendor_root=vendor,
            run_root=output,
            decoder=lambda _: ([row], set(row)),
            require_actor=False,
        )
