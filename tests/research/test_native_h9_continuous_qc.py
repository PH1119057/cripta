"""Regression and negative tests for bounded historical native H9 continuous timer QC."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SYMBOL = "LTCUSDT"
DAY1 = "2025-04-01"
DAY2 = "2025-04-02"
UTC = timezone.utc
ENTRY_TIME = datetime(2025, 4, 1, 10, 0, 20, tzinfo=UTC).timestamp()
LOSS_TIME = datetime(2025, 4, 1, 10, 10, 10, tzinfo=UTC).timestamp()
EXIT_TIME = datetime(2025, 4, 2, 4, 10, 15, tzinfo=UTC).timestamp()
LOSS_BUCKET = int(LOSS_TIME // 300) * 300


@pytest.fixture(scope="module")
def validator():
    path = (Path(__file__).resolve().parents[2] / "research" / "history"
            / "native_h9_continuous_qc.py")
    spec = importlib.util.spec_from_file_location("native_h9_continuous_qc", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gz_csv(path, fields, rows):
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def sample(tmp_path, monkeypatch, validator):
    root = tmp_path / "inputs"
    source_day1 = root / SYMBOL / "public_trades" / (SYMBOL + DAY1 + ".csv.gz")
    source_day2 = root / SYMBOL / "public_trades" / (SYMBOL + DAY2 + ".csv.gz")
    source_day1.parent.mkdir(parents=True)
    qc_path = tmp_path / "qc_full.json"
    frozen_root = tmp_path / "frozen"
    frozen_root.mkdir()
    frozen = frozen_root / ("states_" + SYMBOL + ".csv.gz")
    entry_root = tmp_path / "entry"
    entry_manifest = entry_root / SYMBOL / "manifest.json"
    entry_manifest.parent.mkdir(parents=True)
    ledger = tmp_path / "trade_ledger.jsonl"
    result = tmp_path / "result.json"
    h9_code = tmp_path / "p90l90_h9_continuity_18_24_36_48_second48_v1.py"
    soft_code = tmp_path / "p90l90_soft_stop_2025_183d_replay_v1.py"
    h9_code.write_text("# frozen historical H9 fixture only")
    soft_code.write_text("# frozen historical SOFT fixture only")
    monkeypatch.setattr(validator, "H9_SOURCE_SHA", hash_file(h9_code))
    monkeypatch.setattr(validator, "SOFT_SOURCE_SHA", hash_file(soft_code))

    def write_original(*, side="LONG", middle=None, last=None):
        if middle is None:
            middle = "9.9" if side == "LONG" else "10.1"
        if last is None:
            last = "9.9" if side == "LONG" else "10.1"
        gz_csv(source_day1, ("symbol", "timestamp", "price", "trdMatchID"), [
            {"symbol": SYMBOL, "timestamp": str(ENTRY_TIME),
             "price": "10.0", "trdMatchID": "entry-id"},
            {"symbol": SYMBOL, "timestamp": str(LOSS_TIME),
             "price": middle, "trdMatchID": "loss-id"},
        ])
        gz_csv(source_day2, ("symbol", "timestamp", "price", "trdMatchID"), [
            {"symbol": SYMBOL, "timestamp": str(EXIT_TIME),
             "price": last, "trdMatchID": "exit-id"},
        ])
        qc_path.write_text(json.dumps({
            "stage": "ACQUISITION_QC_PASS",
            "by_day": {
                SYMBOL + "/public_trades/" + p.name: {
                    "rows": 2 if p == source_day1 else 1,
                    "size_bytes": p.stat().st_size,
                    "sha256": hash_file(p),
                } for p in (source_day1, source_day2)
            },
        }))

    def write_frozen(*, side="LONG", recover=False, missing=False,
                     high_override=None, raw_soft=False):
        source_high, source_low = (
            ("9.9", "9.7") if side == "LONG" else ("10.3", "10.1")
        )
        first = datetime.fromisoformat(DAY1).replace(tzinfo=UTC)
        last = datetime(2025, 4, 2, 5, 0, tzinfo=UTC)
        recover_bucket = int(datetime(
            2025, 4, 1, 20, 0, tzinfo=UTC).timestamp())
        rows = []
        for n in range(1, int((last - first).total_seconds() // 300) + 1):
            b = int(first.timestamp()) + 300 * n
            if missing and b == LOSS_BUCKET + 300:
                continue
            h, l = source_high, source_low
            if recover and b == recover_bucket:
                h, l = ("10.5", l) if side == "LONG" else (h, "9.5")
            if high_override is not None:
                h = high_override
            r_high, r_low = h, l
            if raw_soft:
                r_high, r_low = (
                    ("9.5", "9.1") if side == "LONG" else ("10.8", "10.5")
                )
            rows.append({
                "state_effective_ts": b, "source_bar_end_ts": b,
                "range_low": r_low, "range_high": r_high,
                "working_width_pct_lower": "4.5" if raw_soft else "2.0",
                "source_high": h, "source_low": l,
            })
        gz_csv(frozen, ("state_effective_ts", "source_bar_end_ts",
                       "range_low", "range_high", "working_width_pct_lower",
                       "source_high", "source_low"), rows)
        entry_manifest.write_text(json.dumps({
            "symbol": SYMBOL, "PREV7D_WIDTH_LOOKAHEAD": False,
            "frozen_l53_sha256": hash_file(frozen),
        }))

    base_trade = {
        "symbol": SYMBOL, "side": "LONG", "entry_ts": str(ENTRY_TIME),
        "exit_ts": str(EXIT_TIME), "entry_source_order": 1,
        "exit_source_order": 3, "entry_price": "10.0",
        "exit_price": "9.9", "entry_trade_id": "entry-id",
        "exit_reason": "SOFT_STOP", "soft_stop_cause": "H9_LOSS_CONTINUOUS",
        "first_h9_loss_ts": LOSS_BUCKET, "h9_horizon_hours": 18,
        "maker_exit": False, "portfolio_scenario": "H9CONT_18H_S48_OFF",
    }

    def write_record(**updates):
        trade = {**base_trade, **updates}
        ledger.write_text(json.dumps(trade) + "\n")
        result.write_text(json.dumps({
            "status": "QC_PASS", "ledger_rows": 1,
            "ledger_sha256": hash_file(ledger),
            "source_snapshot_sha256": {
                h9_code.name: hash_file(h9_code),
                soft_code.name: hash_file(soft_code),
                "p90l90_tp_only_second48_v1.py": validator.TP_SOURCE_SHA,
                "p90l90_exit_90d_v2.py": validator.BASE_SOURCE_SHA,
            },
        }))

    write_original()
    write_frozen()
    write_record()
    args = argparse.Namespace(
        qc_manifest=str(qc_path), input_root=str(root),
        entry_manifest_root=str(entry_root), frozen_root=str(frozen_root),
        ledger=str(ledger), ledger_result=str(result), h9_source=str(h9_code),
        soft_source=str(soft_code), case=[SYMBOL + ":1:3"],
    )
    return args, write_original, write_frozen, write_record, (
        source_day1, source_day2, frozen, ledger, h9_code, soft_code
    )


def test_long_cross_day_exact_first_h9_event_and_no_file_write(validator, sample):
    args, _, _, _, paths = sample
    assert validator.self_test()["status"] == "SELFTEST_PASS"
    original = [(p.stat().st_mtime_ns, hash_file(p)) for p in paths]
    data = validator.run(args)
    assert data["status"] == "PASS_BOUNDED_H9_CONTINUOUS_FIRST_EXIT"
    assert data["cases_count"] == 1
    assert data["original_day_files"] == 2
    assert data["original_full_day_trade_rows"] == 3
    assert data["cases"][0]["first_h9_loss_ts"] == LOSS_BUCKET
    assert data["cases"][0]["exit_source_order"] == 3
    assert data["cases"][0]["h9_adverse_starts"] == 1
    assert data["cases"][0]["h9_recovery_resets"] == 0
    assert data["writes"] == 0
    assert original == [(p.stat().st_mtime_ns, hash_file(p)) for p in paths]


def test_short_cross_day_uses_lower_h9_raw_union(validator, sample):
    args, write_original, write_frozen, write_record, _ = sample
    write_original(side="SHORT")
    write_frozen(side="SHORT")
    write_record(side="SHORT", exit_price="10.1")
    result = validator.run(args)
    assert result["cases_count"] == 1
    assert result["cases"][0]["side"] == "SHORT"
    assert result["cases"][0]["exit_price"] == "10.1"


def test_earlier_hard_stop_takes_precedence(validator, sample):
    args, write_original, _, _, _ = sample
    write_original(middle="8.9")
    with pytest.raises(ValueError, match="H9_FIRST_EXIT_LEDGER_MISMATCH"):
        validator.run(args)


def test_earlier_raw_soft_preempts_h9_timer(validator, sample):
    args, _, write_frozen, _, _ = sample
    write_frozen(raw_soft=True)
    with pytest.raises(ValueError, match="H9_FIRST_EXIT_LEDGER_MISMATCH"):
        validator.run(args)


def test_market_condition_tp_takes_precedence(validator, sample):
    args, write_original, write_frozen, _, _ = sample
    write_original(middle="10.8")
    write_frozen(high_override="10.7")
    with pytest.raises(ValueError, match="H9_FIRST_EXIT_LEDGER_MISMATCH"):
        validator.run(args)


def test_favorable_h9_reset_cancels_original_timer(validator, sample):
    args, _, write_frozen, _, _ = sample
    write_frozen(recover=True)
    with pytest.raises(ValueError, match="H9_NATIVE_COMPACT_FIRST_EVENT_DRIFT"):
        validator.run(args)


def test_wrong_saved_first_adverse_timestamp_fails(validator, sample):
    args, _, _, write_record, _ = sample
    write_record(first_h9_loss_ts=LOSS_BUCKET + 300)
    with pytest.raises(ValueError, match="H9_FIRST_ADVERSE_TIMER_START_MISMATCH"):
        validator.run(args)


def test_missing_5m_state_is_not_interpolated(validator, sample):
    args, _, write_frozen, _, _ = sample
    write_frozen(missing=True)
    with pytest.raises(ValueError, match="FROZEN_5M_SOURCE_GAP_OR_REORDER"):
        validator.run(args)


def test_modified_native_archive_fails_original_hash(validator, sample):
    args, _, _, _, paths = sample
    paths[1].write_bytes(paths[1].read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="ORIGINAL_PUBLIC_TRADE_DAY_SIZE_DRIFT"):
        validator.run(args)


def test_changed_frozen_model_sha_fails_before_replay(validator, sample):
    args, _, _, _, paths = sample
    paths[4].write_text("# changed frozen research H9 model")
    with pytest.raises(ValueError, match="FROZEN_H9_SOURCE_FILE_DRIFT"):
        validator.run(args)


def test_wrong_entry_trade_id_fails_anchor(validator, sample):
    args, _, _, write_record, _ = sample
    write_record(entry_trade_id="wrong")
    with pytest.raises(ValueError, match="ORIGINAL_ENTRY_ANCHOR_MISMATCH"):
        validator.run(args)


def test_unknown_or_not_h9_exit_cause_fails(validator, sample):
    args, _, _, write_record, _ = sample
    write_record(soft_stop_cause="L53_RAW_MINUS4_WIDTH4")
    with pytest.raises(ValueError, match="NOT_FROZEN_H9_18H_SOFT_STOP"):
        validator.run(args)


def test_duplicate_case_selector_fails(validator, sample):
    args, _, _, _, _ = sample
    args.case = [SYMBOL + ":1:3"] * 2
    with pytest.raises(ValueError, match="CASE_COUNT_OR_DUPLICATE_INVALID"):
        validator.run(args)


def test_only_adjacent_utc_days_allowed(validator, sample):
    args, _, _, write_record, _ = sample
    write_record(exit_ts=str(EXIT_TIME + 86400))
    with pytest.raises(ValueError, match="NOT_BOUNDED_ADJACENT_CROSS_DAY_H9"):
        validator.run(args)


def test_same_timestamp_source_order_preserved(validator):
    row = {"side": "LONG", "entry_price": "10.0",
           "entry_source_order": 1, "entry_ts": "300.25"}
    state = {300: (validator.D("8"), validator.D("11"), validator.D("2"),
                   validator.D("8"), validator.D("11"))}
    out, _ = validator.first_exit(
        row, state, [(1, "300.25", "10.0"), (2, "300.25", "8.9"),
                     (3, "300.25", "10.8")])
    assert out is not None
    assert out["reason"] == "HARD_STOP"
    assert out["source_order"] == 2
