"""Regression tests for the bounded, read-only RESEARCH native-ledger pointer validator."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

DAY = "2025-04-01"
START = "1743465600.0"
SYMS = ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")


@pytest.fixture(scope="module")
def validator():
    source = (
        Path(__file__).resolve().parents[2]
        / "research"
        / "history"
        / "native_ledger_pointer_qc.py"
    )
    spec = importlib.util.spec_from_file_location("native_ledger_pointer_qc", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / "inputs"
    qc = {"stage": "ACQUISITION_QC_PASS", "by_day": {}}
    for sym in SYMS:
        folder = root / sym / "public_trades"
        folder.mkdir(parents=True)
        source = folder / (sym + DAY + ".csv.gz")
        with gzip.open(source, "wt", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=("timestamp", "price", "trdMatchID", "symbol")
            )
            writer.writeheader()
            writer.writerow(
                {"timestamp": START, "price": "10.0",
                 "trdMatchID": sym + "-entry", "symbol": sym}
            )
            # Same timestamp but different native order; a timestamp sort is invalid.
            writer.writerow(
                {"timestamp": START, "price": "10.1",
                 "trdMatchID": sym + "-exit", "symbol": sym}
            )
        qc["by_day"][
            sym + "/public_trades/" + source.name
        ] = {
            "rows": 2,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "size_bytes": source.stat().st_size,
        }
    manifest_path = tmp_path / "qc_full.json"
    manifest_path.write_text(json.dumps(qc))
    ledger = tmp_path / "trade_ledger.jsonl"
    row = {
        "symbol": "LTCUSDT", "side": "LONG",
        "entry_ts": START, "entry_source_order": 1,
        "entry_price": "10.0", "entry_trade_id": "LTCUSDT-entry",
        "exit_ts": START, "exit_source_order": 2,
        "exit_price": "10.05", "exit_reason": "RESTING_TP",
        "maker_exit": True, "portfolio_scenario": "FIXTURE",
    }
    result = tmp_path / "result.json"

    def refresh(updated_row):
        ledger.write_text(json.dumps(updated_row) + "\n")
        result.write_text(json.dumps({
            "status": "QC_PASS", "ledger_rows": 1,
            "ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
        }))

    refresh(row)
    args = argparse.Namespace(
        date=[DAY], qc_manifest=str(manifest_path), input_root=str(root),
        ledger=str(ledger), ledger_result=str(result)
    )
    return args, root, row, refresh


def test_self_test_and_exact_same_timestamp_sequence(validator, sample):
    args, root, _, _ = sample
    assert validator.self_test()["status"] == "SELFTEST_PASS"
    before = {
        path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*.gz")
    }
    out = validator.run(args)
    assert out["status"] == "PASS_BOUNDED_EVENT_POINTER_QC"
    assert out["native_rows"] == 10
    assert out["source_files"] == 5
    assert out["distinct_ledger_event_identities"] == 2
    assert out["ledger_references_with_scenario_repeats"] == 2
    assert out["comparisons"]["ENTRY_EXACT"] == 1
    assert out["comparisons"]["RESTING_TP_CROSSED"] == 1
    assert out["comparisons"]["RESTING_TP_PRICE_DIFF_EXPECTED"] == 1
    after = {
        path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*.gz")
    }
    assert before == after  # input archives are never rewritten


def test_rejects_corrupt_entry_trade_id(validator, sample):
    args, _, row, refresh = sample
    refresh({**row, "entry_trade_id": "WRONG"})
    with pytest.raises(ValueError, match="ENTRY_TRADE_ID_MISMATCH"):
        validator.run(args)


def test_rejects_corrupt_native_hash(validator, sample):
    args, root, _, _ = sample
    source = root / "LTCUSDT" / "public_trades" / ("LTCUSDT" + DAY + ".csv.gz")
    source.write_bytes(source.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="SOURCE_SIZE_MISMATCH"):
        validator.run(args)


def test_rejects_zero_based_ordinal(validator, sample):
    args, _, row, refresh = sample
    refresh({**row, "entry_source_order": 0})
    with pytest.raises(ValueError, match="SOURCE_ORDINAL_BEFORE_DAY"):
        validator.run(args)


def test_rejects_non_crossed_resting_tp(validator, sample):
    args, _, row, refresh = sample
    refresh({**row, "exit_price": "10.2"})
    with pytest.raises(ValueError, match="RESTING_TP_NOT_CROSSED"):
        validator.run(args)


def test_rejects_more_than_three_sample_dates(validator, sample):
    args, _, _, _ = sample
    args.date = ["2025-04-01", "2025-04-02", "2025-04-03", "2025-04-04"]
    with pytest.raises(ValueError, match="DATE_COUNT_OUT_OF_BOUNDS"):
        validator.run(args)


def test_short_limit_crossing_and_hard_stop(validator):
    row = {"timestamp": "100.25", "price": "8.9900", "trdMatchID": "id"}
    short = {
        "exit_ts": "100.25", "exit_price": "9.00",
        "exit_reason": "RESTING_TP", "side": "SHORT", "maker_exit": True,
    }
    assert validator.check_event("exit", short, row) == ("RESTING_TP_CROSSED", True)
    hard = {
        "exit_ts": "100.25", "exit_price": "8.9900", "exit_reason": "HARD_STOP"
    }
    assert validator.check_event("exit", hard, row)[0] == "HARD_STOP_EXACT"


def _same_day_hardstop_sample(sample, prices, *, side="LONG"):
    """Synthetic archive with an exact one-based same-timestamp native hardstop."""
    args, root, row, refresh = sample
    sym = "LTCUSDT"
    source = root / sym / "public_trades" / (sym + DAY + ".csv.gz")
    with gzip.open(source, "wt", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("timestamp", "price", "trdMatchID", "symbol")
        )
        writer.writeheader()
        for i, price in enumerate(prices):
            trade_id = sym + "-entry" if i == 0 else sym + "-print-" + str(i)
            writer.writerow({
                "timestamp": START, "price": price,
                "trdMatchID": trade_id, "symbol": sym,
            })
    manifest_file = Path(args.qc_manifest)
    manifest = json.loads(manifest_file.read_text())
    source_key = sym + "/public_trades/" + source.name
    manifest["by_day"][source_key] = {
        "rows": len(prices),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "size_bytes": source.stat().st_size,
    }
    manifest_file.write_text(json.dumps(manifest))
    refresh({
        **row, "side": side,
        "exit_source_order": len(prices),
        "exit_price": prices[-1], "exit_reason": "HARD_STOP",
        "maker_exit": False,
    })
    result_file = Path(args.ledger_result)
    result = json.loads(result_file.read_text())
    result["source_snapshot_sha256"] = {
        "p90l90_exit_90d_v2.py": validator_hardstop_source_sha()
    }
    result_file.write_text(json.dumps(result))
    args.verify_hardstop_first_hit = True
    return args


def validator_hardstop_source_sha():
    # Frozen historical research model hash, not a production-execution capability.
    return "3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10"


def test_same_timestamp_long_hardstop_first_crossing(validator, sample):
    args = _same_day_hardstop_sample(sample, ["10.0", "9.5", "8.9"])
    result = validator.run(args)
    assert result["comparisons"]["HARD_STOP_EXACT"] == 1
    assert result["hardstop_first_hit"]["unique_same_day_first_crossings"] == 1
    assert result["hardstop_first_hit"]["scenario_references"] == 1
    assert result["hardstop_first_hit"]["result"] == (
        "PASS_FIRST_CROSSING_SOURCE_AND_COMPACT"
    )


def test_same_timestamp_short_hardstop_first_crossing(validator, sample):
    args = _same_day_hardstop_sample(
        sample, ["10.0", "10.5", "11.1"], side="SHORT"
    )
    result = validator.run(args)
    assert result["hardstop_first_hit"]["unique_same_day_first_crossings"] == 1


def test_rejects_hardstop_when_earlier_crossing_existed(validator, sample):
    args = _same_day_hardstop_sample(sample, ["10.0", "8.9", "8.8"])
    # Event-pointers by themselves match the third row, but the FIRST hit was row 2.
    with pytest.raises(ValueError, match="HARDSTOP_FIRST_CROSSING_NATIVE_MISMATCH"):
        validator.run(args)


def test_rejects_unpinned_historical_stop_model(validator, sample):
    args = _same_day_hardstop_sample(sample, ["10.0", "9.5", "8.9"])
    result_file = Path(args.ledger_result)
    record = json.loads(result_file.read_text())
    record["source_snapshot_sha256"]["p90l90_exit_90d_v2.py"] = "0" * 64
    result_file.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="HARDSTOP_MODEL_SOURCE_UNPINNED"):
        validator.run(args)
