"""Issue #158 original liquidation Parquet ↔ normalized parity tests."""
import csv
import gzip
import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/"
    "r158_liquidation_parquet_parity_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_liq_parity", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SYMBOL = "APTUSDT"
DAY = date(2026, 5, 18)
HOUR = "00"
COMMIT = "e" * 40


def raw_row():
    start = int(
        datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp()
    )
    received_ns = (start + 12) * 1_000_000_000 + 345_678_000
    trade_ms = (start + 11) * 1000 + 222
    return {
        "received_time": received_ns,
        "event_time": trade_ms * 1_000_000,
        "symbol": SYMBOL,
        "side": "BUY",
        "order_type": "MARKET",
        "time_in_force": "IOC",
        "quantity": "2.50",
        "price": "5.10",
        "average_price": "5.10",
        "order_status": "FILLED",
        "last_filled_quantity": "2.50",
        "filled_quantity": "2.50",
        "trade_time": trade_ms,
    }


def normalized_row(*, position_side="SHORT"):
    row = raw_row()
    source = f"raw/{DAY}/{HOUR}/{SYMBOL}_liquidations.parquet.zst"
    return {
        "observed_timestamp_us": str(row["received_time"] // 1000),
        "trade_timestamp_us": str(row["trade_time"] * 1000),
        "symbol": SYMBOL,
        "liquidated_position_side": position_side,
        "price": "5.10",
        "size": "2.50",
        "order_status": "FILLED",
        "source_path": source,
    }


def stage_vendor(root: Path, *, duplicate_receipt=False, corrupt_receipt_sha=False):
    raw_path = root / "raw" / str(DAY) / HOUR / f"{SYMBOL}_liquidations.parquet.zst"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"synthetic-zstd-parquet-envelope")
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if corrupt_receipt_sha:
        digest = "0" * 64
    item = {
        "key": f"bybit/{DAY}/{HOUR}/{SYMBOL}_liquidations.parquet.zst",
        "symbol": SYMBOL,
        "date": str(DAY),
        "hour": HOUR,
        "bytes": raw_path.stat().st_size,
        "sha256": digest,
    }
    root.mkdir(parents=True, exist_ok=True)
    with (root / "OBJECTS.jsonl").open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(item) + "\n")
        if duplicate_receipt:
            stream.write(json.dumps(item) + "\n")
    return raw_path


def stage_normalized(root: Path, *, position_side="SHORT"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{SYMBOL}.csv.gz"
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
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(normalized_row(position_side=position_side))
    return path


def test_full_row_parity_pass_with_injected_independent_decoder(tmp_path):
    vendor = tmp_path / "vendor"
    normalized = tmp_path / "normalized"
    stage_vendor(vendor)
    stage_normalized(normalized)
    output_parent = tmp_path / "runs"
    output_parent.mkdir()
    output = output_parent / "parity"

    row = raw_row()

    def decoder(_):
        return [row], set(row)

    report = m.audit(
        COMMIT,
        vendor_root=vendor,
        normalized_root=normalized,
        output_root=output,
        cohort=(SYMBOL,),
        decoder=decoder,
        expected_receipts=1,
        expected_normalized_rows=1,
        require_actor=False,
    )

    assert report["status"] == "ORIGINAL_PARQUET_NORMALIZED_FULL_ROW_PARITY_PASS"
    assert report["objects"] == 1
    assert report["raw_rows"] == 1
    assert report["normalized_rows"] == 1
    assert report["research_ready"] is False
    assert report["vendor_received_time_is_not_cripta_available_after"] is True
    assert report["bankruptcy_price_is_not_execution_price"] is True
    saved = json.loads(
        (output / "parquet_normalized_parity_report.json").read_text()
    )
    assert saved == report


def test_normalized_side_mismatch_fails_before_output_creation(tmp_path):
    vendor = tmp_path / "vendor"
    normalized = tmp_path / "normalized"
    stage_vendor(vendor)
    stage_normalized(normalized, position_side="LONG")
    output_parent = tmp_path / "runs"
    output_parent.mkdir()
    output = output_parent / "parity"
    row = raw_row()

    with pytest.raises(ValueError, match="PARQUET_NORMALIZED_ROW_MISMATCH"):
        m.audit(
            COMMIT,
            vendor_root=vendor,
            normalized_root=normalized,
            output_root=output,
            cohort=(SYMBOL,),
            decoder=lambda _: ([row], set(row)),
            expected_receipts=1,
            expected_normalized_rows=1,
            require_actor=False,
        )
    assert not output.exists()


def test_receipt_sha_mismatch_fails_before_decoder(tmp_path):
    vendor = tmp_path / "vendor"
    normalized = tmp_path / "normalized"
    stage_vendor(vendor, corrupt_receipt_sha=True)
    stage_normalized(normalized)
    output_parent = tmp_path / "runs"
    output_parent.mkdir()
    called = False

    def decoder(_):
        nonlocal called
        called = True
        return [], set()

    with pytest.raises(ValueError, match="RAW_OBJECT_SHA256_MISMATCH"):
        m.audit(
            COMMIT,
            vendor_root=vendor,
            normalized_root=normalized,
            output_root=output_parent / "parity",
            cohort=(SYMBOL,),
            decoder=decoder,
            expected_receipts=1,
            expected_normalized_rows=1,
            require_actor=False,
        )
    assert called is False


def test_duplicate_receipt_key_fails_closed(tmp_path):
    vendor = tmp_path / "vendor"
    normalized = tmp_path / "normalized"
    stage_vendor(vendor, duplicate_receipt=True)
    stage_normalized(normalized)
    output_parent = tmp_path / "runs"
    output_parent.mkdir()

    with pytest.raises(ValueError, match="DUPLICATE_OR_INVALID_OBJECT_KEY"):
        m.audit(
            COMMIT,
            vendor_root=vendor,
            normalized_root=normalized,
            output_root=output_parent / "parity",
            cohort=(SYMBOL,),
            decoder=lambda _: ([], set()),
            expected_receipts=None,
            expected_normalized_rows=1,
            require_actor=False,
        )


def test_bybit_placeholder_invariant_drift_fails(tmp_path):
    vendor = tmp_path / "vendor"
    normalized = tmp_path / "normalized"
    stage_vendor(vendor)
    stage_normalized(normalized)
    output_parent = tmp_path / "runs"
    output_parent.mkdir()
    row = raw_row()
    row["order_type"] = "LIMIT"

    with pytest.raises(ValueError, match="BYBIT_ORDER_TYPE_PLACEHOLDER_DRIFT"):
        m.audit(
            COMMIT,
            vendor_root=vendor,
            normalized_root=normalized,
            output_root=output_parent / "parity",
            cohort=(SYMBOL,),
            decoder=lambda _: ([row], set(row)),
            expected_receipts=1,
            expected_normalized_rows=1,
            require_actor=False,
        )
