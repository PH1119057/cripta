"""Regression gates for bounded historical RAW L5-3 SOFT_STOP first-event QC."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SYMBOL = "APTUSDT"
DAY = "2025-04-01"
START = "1743465600.25"
MIDDLE = "1743465899.75"
LAST = "1743465900.25"
B0 = 1743465600
B1 = B0 + 300


@pytest.fixture(scope="module")
def checker():
    path = (Path(__file__).resolve().parents[2] / "research"
            / "history" / "native_raw_softstop_qc.py")
    spec = importlib.util.spec_from_file_location("native_raw_softstop_qc", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gz_csv(path, names, rows):
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def sample(tmp_path, monkeypatch, checker):
    raw_root = tmp_path / "inputs"
    raw = raw_root / SYMBOL / "public_trades" / (SYMBOL + DAY + ".csv.gz")
    raw.parent.mkdir(parents=True)
    frozen_root = tmp_path / "frozen"
    frozen_root.mkdir()
    frozen = frozen_root / ("states_" + SYMBOL + ".csv.gz")
    entry_root = tmp_path / "entry"
    entry_manifest = entry_root / SYMBOL / "manifest.json"
    entry_manifest.parent.mkdir(parents=True)
    qc = tmp_path / "qc_full.json"
    ledger = tmp_path / "trade_ledger.jsonl"
    result = tmp_path / "result.json"
    h9_source = tmp_path / "p90l90_h9_continuity_18_24_36_48_second48_v1.py"
    soft_source = tmp_path / "p90l90_soft_stop_2025_183d_replay_v1.py"
    h9_source.write_text("# frozen h9 model fixture")
    soft_source.write_text("# frozen soft model fixture")
    monkeypatch.setattr(checker, "H9_SOURCE_SHA", sha(h9_source))
    monkeypatch.setattr(checker, "SOFT_SOURCE_SHA", sha(soft_source))

    def raw_rows(mid="10.2", end="9.6"):
        gz_csv(raw, ("symbol", "timestamp", "price", "trdMatchID"), [
            {"symbol": SYMBOL, "timestamp": START, "price": "10.0",
             "trdMatchID": "entry-id"},
            {"symbol": SYMBOL, "timestamp": MIDDLE, "price": mid,
             "trdMatchID": "middle-id"},
            {"symbol": SYMBOL, "timestamp": LAST, "price": end,
             "trdMatchID": "exit-id"},
        ])
        qc.write_text(json.dumps({
            "stage": "ACQUISITION_QC_PASS",
            "by_day": {SYMBOL + "/public_trades/" + raw.name: {
                "rows": 3, "size_bytes": raw.stat().st_size,
                "sha256": sha(raw),
            }},
        }))

    def frozen_states(width="4.5", second_end=B1, has_second=True):
        rows = [{
            "state_effective_ts": B0, "source_bar_end_ts": B0,
            "range_low": "8.5", "range_high": "11.0",
            "working_width_pct_lower": "4.2",
        }]
        if has_second:
            rows.append({
                "state_effective_ts": B1, "source_bar_end_ts": second_end,
                "range_low": "8.5", "range_high": "9.5",
                "working_width_pct_lower": width,
            })
        gz_csv(frozen, (
            "state_effective_ts", "source_bar_end_ts",
            "range_low", "range_high", "working_width_pct_lower",
        ), rows)
        entry_manifest.write_text(json.dumps({
            "symbol": SYMBOL, "PREV7D_WIDTH_LOOKAHEAD": False,
            "frozen_l53_sha256": sha(frozen),
        }))

    base = {
        "symbol": SYMBOL, "side": "LONG",
        "entry_ts": START, "exit_ts": LAST,
        "entry_source_order": 1, "exit_source_order": 3,
        "entry_price": "10.0", "exit_price": "9.6",
        "entry_trade_id": "entry-id", "exit_reason": "SOFT_STOP",
        "soft_stop_cause": "L53_RAW_MINUS4_WIDTH4",
        "portfolio_scenario": "H9CONT_18H_S48_OFF",
    }

    def write_trade(**changed):
        ledger.write_text(json.dumps({**base, **changed}) + "\n")
        result.write_text(json.dumps({
            "status": "QC_PASS", "ledger_rows": 1,
            "ledger_sha256": sha(ledger),
            "source_snapshot_sha256": {
                h9_source.name: sha(h9_source),
                soft_source.name: sha(soft_source),
                "p90l90_tp_only_second48_v1.py": checker.TP_SOURCE_SHA,
                "p90l90_exit_90d_v2.py": checker.BASE_SOURCE_SHA,
            },
        }))

    raw_rows()
    frozen_states()
    write_trade()
    args = argparse.Namespace(
        qc_manifest=str(qc), input_root=str(raw_root),
        entry_manifest_root=str(entry_root),
        frozen_root=str(frozen_root), ledger=str(ledger),
        ledger_result=str(result), h9_source=str(h9_source),
        soft_source=str(soft_source), case=[SYMBOL + ":1:3"],
    )
    return args, raw_rows, frozen_states, write_trade, (
        raw, frozen, ledger, h9_source, soft_source
    )


def test_self_test_and_long_first_raw_softstop(checker, sample):
    args, _, _, _, paths = sample
    assert checker.self_test()["status"] == "SELFTEST_PASS"
    before = [(p.stat().st_mtime_ns, sha(p)) for p in paths]
    out = checker.run(args)
    assert out["status"] == "PASS_BOUNDED_RAW_L53_FIRST_SOFTSTOP"
    assert out["cases_count"] == 1
    assert out["whole_native_day_rows_scanned"] == 3
    assert out["writes"] == 0
    assert out["cases"][0]["closed_5m_bucket_transitions"] == 1
    assert out["cases"][0]["reason"] == "SOFT_STOP"
    assert before == [(p.stat().st_mtime_ns, sha(p)) for p in paths]


def test_short_raw_adverse_first_exit(checker, sample):
    args, _, frozen_states, write_trade, _ = sample
    raw = Path(args.input_root) / SYMBOL / "public_trades" / (SYMBOL + DAY + ".csv.gz")
    gz_csv(raw, ("symbol", "timestamp", "price", "trdMatchID"), [
        {"symbol": SYMBOL, "timestamp": START, "price": "10.0", "trdMatchID": "entry-id"},
        {"symbol": SYMBOL, "timestamp": MIDDLE, "price": "10.2", "trdMatchID": "middle-id"},
        {"symbol": SYMBOL, "timestamp": LAST, "price": "10.4", "trdMatchID": "exit-id"},
    ])
    manifest_path = Path(args.qc_manifest)
    manifest = json.loads(manifest_path.read_text())
    key = SYMBOL + "/public_trades/" + raw.name
    manifest["by_day"][key] = {
        "rows": 3, "size_bytes": raw.stat().st_size, "sha256": sha(raw),
    }
    manifest_path.write_text(json.dumps(manifest))
    f = Path(args.frozen_root) / ("states_" + SYMBOL + ".csv.gz")
    gz_csv(f, (
        "state_effective_ts", "source_bar_end_ts",
        "range_low", "range_high", "working_width_pct_lower",
    ), [
        {"state_effective_ts": B0, "source_bar_end_ts": B0,
         "range_low": "9.0", "range_high": "11.5", "working_width_pct_lower": "4.2"},
        {"state_effective_ts": B1, "source_bar_end_ts": B1,
         "range_low": "10.5", "range_high": "11.5", "working_width_pct_lower": "4.5"},
    ])
    entry_manifest = Path(args.entry_manifest_root) / SYMBOL / "manifest.json"
    value = json.loads(entry_manifest.read_text())
    value["frozen_l53_sha256"] = sha(f)
    entry_manifest.write_text(json.dumps(value))
    write_trade(side="SHORT", exit_price="10.4")
    out = checker.run(args)
    assert out["cases_count"] == 1
    assert out["cases"][0]["side"] == "SHORT"


def test_earlier_hardstop_rejected(checker, sample):
    args, raw_rows, _, _, _ = sample
    raw_rows(mid="8.9")
    with pytest.raises(ValueError, match="HISTORICAL_FIRST_SOFTSTOP_MISMATCH"):
        checker.run(args)


def test_earlier_resting_tp_rejected(checker, sample):
    args, raw_rows, _, _, _ = sample
    raw_rows(mid="11.1")
    with pytest.raises(ValueError, match="HISTORICAL_FIRST_SOFTSTOP_MISMATCH"):
        checker.run(args)


def test_width_below_four_does_not_trigger_soft(checker, sample):
    args, _, frozen_states, _, _ = sample
    frozen_states(width="3.99")
    with pytest.raises(ValueError, match="NATIVE_COMPACT_CAUSAL_EXIT_MISMATCH"):
        checker.run(args)


def test_missing_frozen_closed_bucket_rejected(checker, sample):
    args, _, frozen_states, _, _ = sample
    frozen_states(has_second=False)
    with pytest.raises(ValueError, match="FROZEN_L53_STATE_COVERAGE_MISSING"):
        checker.run(args)


def test_noncausal_state_end_rejected(checker, sample):
    args, _, frozen_states, _, _ = sample
    frozen_states(second_end=B1 + 1)
    with pytest.raises(ValueError, match="NONCAUSAL_FROZEN_L53_STATE"):
        checker.run(args)


def test_model_bytes_mismatch_rejected(checker, sample):
    args, _, _, _, paths = sample
    paths[3].write_bytes(paths[3].read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="FROZEN_SOFT_SOURCE_FILE_MISMATCH"):
        checker.run(args)


def test_wrong_original_trade_id_rejected(checker, sample):
    args, _, _, write_trade, _ = sample
    write_trade(entry_trade_id="wrong")
    with pytest.raises(ValueError, match="ORIGINAL_ENTRY_ANCHOR_MISMATCH"):
        checker.run(args)


def test_h9_timer_must_be_excluded_by_less_than_eighteen_hours(checker, sample):
    args, _, _, write_trade, _ = sample
    write_trade(exit_ts="1743530400.25")
    with pytest.raises(ValueError, match="SOFTSTOP_18H_TIMER_NOT_EXCLUDED"):
        checker.run(args)


def test_wrong_soft_cause_rejected(checker, sample):
    args, _, _, write_trade, _ = sample
    write_trade(soft_stop_cause="H9_LOSS_CONTINUOUS")
    with pytest.raises(ValueError, match="SOFTSTOP_CAUSE_UNSUPPORTED"):
        checker.run(args)


def test_reject_duplicate_case_selectors(checker, sample):
    args, _, _, _, _ = sample
    args.case = [SYMBOL + ":1:3", SYMBOL + ":1:3"]
    with pytest.raises(ValueError, match="CASE_COUNT_OR_DUPLICATE_INVALID"):
        checker.run(args)
