"""Negative and preservation tests for bounded historical TPOnly first-exit QC."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SYMBOL = "LTCUSDT"
DAY = "2025-04-01"
T0 = "1743465600.25"
T1 = "1743465899.75"
T2 = "1743465900.25"
B0 = 1743465600
B1 = B0 + 300


@pytest.fixture(scope="module")
def verifier():
    source = (
        Path(__file__).resolve().parents[2]
        / "research"
        / "history"
        / "native_tp_causal_qc.py"
    )
    spec = importlib.util.spec_from_file_location("native_tp_causal_qc", source)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_csv_gz(path, fieldnames, rows):
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "inputs"
    raw = root / SYMBOL / "public_trades" / (SYMBOL + DAY + ".csv.gz")
    raw.parent.mkdir(parents=True)
    meta_path = tmp_path / "qc_full.json"
    frozen_root = tmp_path / "frozen"
    frozen_root.mkdir()
    frozen = frozen_root / ("states_" + SYMBOL + ".csv.gz")
    entry_root = tmp_path / "entry_manifest"
    entry = entry_root / SYMBOL / "manifest.json"
    entry.parent.mkdir(parents=True)
    ledger = tmp_path / "trade_ledger.jsonl"
    result = tmp_path / "result.json"

    def write_raw(mid="10.4"):
        write_csv_gz(raw, ("symbol", "timestamp", "price", "trdMatchID"), [
            {"symbol": SYMBOL, "timestamp": T0, "price": "10.0",
             "trdMatchID": "entry-id"},
            {"symbol": SYMBOL, "timestamp": T1, "price": mid,
             "trdMatchID": "middle-id"},
            {"symbol": SYMBOL, "timestamp": T2, "price": "10.7",
             "trdMatchID": "exit-id"},
        ])
        meta_path.write_text(json.dumps({
            "stage": "ACQUISITION_QC_PASS",
            "by_day": {
                SYMBOL + "/public_trades/" + raw.name: {
                    "rows": 3, "size_bytes": raw.stat().st_size,
                    "sha256": file_hash(raw),
                },
            },
        }))

    def write_frozen(second_bar_end=B1, include_last=True):
        states = [
            {"symbol": SYMBOL, "state_effective_ts": B0,
             "source_bar_end_ts": B0, "range_low": "9.0",
             "range_high": "11.0"},
        ]
        if include_last:
            states.append({
                "symbol": SYMBOL, "state_effective_ts": B1,
                "source_bar_end_ts": second_bar_end, "range_low": "9.0",
                "range_high": "10.6",
            })
        write_csv_gz(frozen, (
            "symbol", "state_effective_ts", "source_bar_end_ts",
            "range_low", "range_high",
        ), states)
        entry.write_text(json.dumps({
            "symbol": SYMBOL, "PREV7D_WIDTH_LOOKAHEAD": False,
            "frozen_l53_sha256": file_hash(frozen),
        }))

    base_trade = {
        "symbol": SYMBOL, "side": "LONG",
        "entry_ts": T0, "exit_ts": T2,
        "entry_source_order": 1, "exit_source_order": 3,
        "entry_price": "10.0", "exit_price": "10.7",
        "entry_trade_id": "entry-id", "exit_reason": "MARKETABLE_TP",
        "maker_exit": False, "portfolio_scenario": "CLASSIC_S48_OFF",
    }

    def write_trade(**changes):
        row = {**base_trade, **changes}
        ledger.write_text(json.dumps(row) + "\n")
        result.write_text(json.dumps({
            "status": "QC_PASS", "ledger_rows": 1,
            "ledger_sha256": file_hash(ledger),
            "source_snapshot_sha256": {
                "p90l90_tp_only_second48_v1.py":
                    "b25210dbd9977fe85f568a52867b3673aea65c17308e19612cb216efae68164e",
                "p90l90_exit_90d_v2.py":
                    "3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10",
                "p90l90_bull2025_portfolio_v1.py":
                    "ebbf6d79bca9d9353b717833becd64eb7f36c88351ec1e77fe41ff152d3f4ee1",
            },
        }))

    write_raw()
    write_frozen()
    write_trade()
    args = argparse.Namespace(
        qc_manifest=str(meta_path), input_root=str(root),
        entry_manifest_root=str(entry_root), frozen_root=str(frozen_root),
        ledger=str(ledger), ledger_result=str(result),
        case=[SYMBOL + ":1:3"],
    )
    return args, write_raw, write_frozen, write_trade, (raw, frozen, ledger)


def test_selftest_and_causal_marketable_from_closed_bucket(verifier, case):
    args, _, _, _, paths = case
    assert verifier.self_test()["status"] == "SELFTEST_PASS"
    before = [(path.stat().st_mtime_ns, file_hash(path)) for path in paths]
    out = verifier.run(args)
    assert out["status"] == "PASS_BOUNDED_CAUSAL_TPONLY_FIRST_EXIT"
    assert out["cases_count"] == 1
    assert out["full_native_day_rows_scanned"] == 3
    assert out["server_writes"] == 0
    assert out["cases"][0]["reason"] == "MARKETABLE_TP"
    assert out["cases"][0]["source_order"] == 3
    assert out["cases"][0]["bucket_transitions"] == 1
    assert out["cases"][0]["tp_target_changes"] == 0
    assert before == [(path.stat().st_mtime_ns, file_hash(path)) for path in paths]


def test_earlier_resting_tp_is_detected_not_silently_accepted(verifier, case):
    args, write_raw, _, _, _ = case
    # Before the new bucket this print crosses the *previous* resting target.
    write_raw(mid="11.1")
    with pytest.raises(ValueError, match="HISTORICAL_FIRST_EXIT_EVENT_MISMATCH"):
        verifier.run(args)


def test_earlier_hardstop_takes_priority_over_tp(verifier, case):
    args, write_raw, _, _, _ = case
    write_raw(mid="8.9")
    with pytest.raises(ValueError, match="HISTORICAL_FIRST_EXIT_EVENT_MISMATCH"):
        verifier.run(args)


def test_model_source_pin_failure(verifier, case):
    args, _, _, _, _ = case
    report = Path(args.ledger_result)
    value = json.loads(report.read_text())
    value["source_snapshot_sha256"]["p90l90_tp_only_second48_v1.py"] = "0" * 64
    report.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="FROZEN_TP_MODEL_SOURCE_UNPINNED"):
        verifier.run(args)


def test_original_source_tamper_fails_immutable_hash(verifier, case):
    args, _, _, _, paths = case
    raw = paths[0]
    raw.write_bytes(raw.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="ORIGINAL_SOURCE_SHA_MISMATCH"):
        verifier.run(args)


def test_noncausal_frozen_bar_timestamp_fails(verifier, case):
    args, _, write_frozen, _, _ = case
    write_frozen(second_bar_end=B1 + 1)
    with pytest.raises(ValueError, match="NONCAUSAL_L53_STATE"):
        verifier.run(args)


def test_missing_closed_bucket_state_is_not_guessed(verifier, case):
    args, _, write_frozen, _, _ = case
    write_frozen(include_last=False)
    with pytest.raises(ValueError, match="FROZEN_L53_BUCKET_COVERAGE_MISSING"):
        verifier.run(args)


def test_entry_trade_id_drift_fails(verifier, case):
    args, _, _, write_trade, _ = case
    write_trade(entry_trade_id="wrong")
    with pytest.raises(ValueError, match="ORIGINAL_ENTRY_ANCHOR_MISMATCH"):
        verifier.run(args)


def test_case_selector_is_bounded(verifier, case):
    args, _, _, _, _ = case
    args.case = [SYMBOL + ":1:3"] * 5
    with pytest.raises(ValueError, match="CASE_COUNT_OR_DUPLICATE_INVALID"):
        verifier.run(args)


def test_short_side_market_target_uses_lower_raw_boundary(verifier):
    record = {"side": "SHORT", "entry_price": "10.0",
              "entry_source_order": 1, "entry_ts": "300.25"}
    state = {300: (verifier.Decimal("9.0"), verifier.Decimal("11.0")),
             600: (verifier.Decimal("9.6"), verifier.Decimal("11.0"))}
    first, _ = verifier.first_exit(record, state, [
        (1, "300.25", "10.0"), (2, "599.75", "9.8"),
        (3, "600.25", "9.5"),
    ])
    assert first is not None
    assert first["reason"] == "MARKETABLE_TP"
    assert first["new_closed_bucket_target"] == "9.6"
