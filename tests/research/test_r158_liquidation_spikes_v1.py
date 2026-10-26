"""Synthetic receipt, source-hour, timestamp and count/notional tests for #158 #24."""
import csv
import gzip
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

SRC = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_liquidation_spikes_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_liq", SRC)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _setup(tmp_path):
    vendor = tmp_path / "vendor"
    norm = tmp_path / "normalized"
    original = vendor / "raw/2026-05-19/00/APTUSDT_liquidations.parquet.zst"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"original-vendor-fixture-not-real-parquet")
    rel_key = "bybit/2026-05-19/00/APTUSDT_liquidations.parquet.zst"
    row = {
        "key": rel_key, "symbol": "APTUSDT", "date": "2026-05-19", "hour": "00",
        "bytes": original.stat().st_size,
        "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
    }
    (vendor / "OBJECTS.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    norm.mkdir()
    normal = norm / "APTUSDT.csv.gz"
    fields = [
        "observed_timestamp_us", "symbol", "source_path", "price", "size",
        "liquidated_position_side", "order_status"
    ]
    first = int(datetime(2026, 5, 19, tzinfo=UTC).timestamp()) * 1000000
    events = [
        [first, "APTUSDT", "raw/" + rel_key[6:], "2", "3", "LONG", "FILLED"],
        [first + 10000000, "APTUSDT", "raw/" + rel_key[6:], "4", "1", "SHORT", "FILLED"],
        [first + 300000000, "APTUSDT", "raw/" + rel_key[6:], "10", "2", "LONG", "FILLED"],
    ]
    with gzip.open(normal, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        writer.writerows(events)
    return vendor, norm, original, row, fields, events


def _repack(path, fields, events):
    with gzip.open(path, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        writer.writerows(events)


def test_normalized_covers_native_objects_not_missing_hours(tmp_path):
    vendor, norm, original, receipt, columns, rows = _setup(tmp_path)
    result = m.calculate("APTUSDT", vendor, norm)
    assert result["original_object_hours"] == 1
    assert result["missing_original_object_hours"] == 719
    assert result["missing_original_object_hours_status"] == "UNKNOWN_NOT_ZERO"
    assert result["normalized_rows"] == 3
    assert result["normalized_source_objects_used"] == 1
    assert result["published_price_x_quantity_usdt"] == "30"
    assert result["liquidated_position_sides"] == {"LONG": 2, "SHORT": 1}
    one = result["windows"]["1m"]
    five = result["windows"]["5m"]
    fifteen = result["windows"]["15m"]
    assert one["observed_source_file_bins"] == 60
    assert one["bins_with_no_file"] == 719 * 60
    assert one["bins_with_records"] == 2
    assert one["max_count"] == 2
    assert one["max_notional_usdt"] == "20"
    assert five["bins_with_records"] == 2
    assert five["max_count"] == 2
    assert fifteen["bins_with_records"] == 1
    assert fifteen["max_count"] == 3
    assert result["cripta_local_available_after"] is None
    assert result["native_parquet_row_parity"] == "NOT_VERIFIED"


def test_original_sha256_mismatch_fails_closed(tmp_path):
    vendor, norm, original, receipt, columns, rows = _setup(tmp_path)
    original.write_bytes(b"X" + original.read_bytes()[1:])
    with pytest.raises(ValueError, match="sha256 mismatch"):
        m.calculate("APTUSDT", vendor, norm)


def test_normalized_unverified_source_cannot_be_counted(tmp_path):
    vendor, norm, original, receipt, columns, rows = _setup(tmp_path)
    rows[0][2] = "raw/2026-05-19/01/APTUSDT_liquidations.parquet.zst"
    _repack(norm / "APTUSDT.csv.gz", columns, rows)
    with pytest.raises(ValueError, match="no verified original object"):
        m.calculate("APTUSDT", vendor, norm)


def test_vendor_hour_mismatch_fails_closed(tmp_path):
    vendor, norm, original, receipt, columns, rows = _setup(tmp_path)
    rows[0][0] += 3600 * 1000000
    _repack(norm / "APTUSDT.csv.gz", columns, rows)
    with pytest.raises(ValueError, match="outside original object hour"):
        m.calculate("APTUSDT", vendor, norm)


def test_unknown_volume_and_symbol_fail_closed(tmp_path):
    vendor, norm, original, receipt, columns, rows = _setup(tmp_path)
    rows[0][4] = "NaN"
    _repack(norm / "APTUSDT.csv.gz", columns, rows)
    with pytest.raises(ValueError, match="invalid positive"):
        m.calculate("APTUSDT", vendor, norm)
    rows[0][4] = "3"
    rows[0][1] = "DOTUSDT"
    _repack(norm / "APTUSDT.csv.gz", columns, rows)
    with pytest.raises(ValueError, match="symbol mismatch"):
        m.calculate("APTUSDT", vendor, norm)


def test_original_receipt_hour_must_match_native_object_hour(tmp_path):
    vendor, norm, original, receipt, columns, rows = _setup(tmp_path)
    receipt["hour"] = "01"
    (vendor / "OBJECTS.jsonl").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="receipt hour"):
        m.calculate("APTUSDT", vendor, norm)
