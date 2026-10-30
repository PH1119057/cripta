"""Issue #158 first-month #10/#16 materializer contract tests."""
import gzip
import importlib.util
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_firstmonth_10_16_materialize_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_materializer", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SYMBOL = "APTUSDT"
START = date(2026, 5, 18)
COMMIT = "a" * 40


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
    item = snapshot(day)
    item["type"] = "delta"
    item["ts"] += 100
    item["data"] = {
        "s": SYMBOL,
        "u": 101,
        "seq": 1001,
        "b": [["300", "12"]],
        "a": [["310", "9"]],
    }
    return item


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


def count_gzip_jsonl(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return sum(1 for _ in stream)


def test_two_day_materialization_is_immutable_and_self_describing(tmp_path):
    raw_root = tmp_path / "raw"
    for offset in range(2):
        stage_day(raw_root, START + timedelta(days=offset))
    runs_parent = tmp_path / "runs"
    runs_parent.mkdir()
    output_root = runs_parent / "r158"

    final = m.materialize_symbol(
        SYMBOL,
        COMMIT,
        raw_root=raw_root,
        output_root=output_root,
        days=2,
        require_actor=False,
    )

    target = output_root / SYMBOL
    assert final["status"] == "PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
    assert final["research_ready"] is False
    assert final["indicator_10"]["formula_version"] == m.EXPECTED_OFI_VERSION
    assert final["indicator_16"]["formula_version"] == m.EXPECTED_ABSORPTION_VERSION
    assert final["indicator_16"]["classification"] == (
        "ABSORPTION_EVIDENCE_PROXY_COMPONENTS_ONLY"
    )
    assert final["indicator_10"]["rows"] == 2880
    assert final["indicator_16"]["rows"] == 2880
    assert count_gzip_jsonl(target / "indicator_10_ofi_1m.jsonl.gz") == 2880
    assert count_gzip_jsonl(
        target / "indicator_16_absorption_components_1m.jsonl.gz"
    ) == 2880

    started = json.loads((target / "run_start.json").read_text())
    completed = json.loads((target / "run_final.json").read_text())
    provenance = json.loads((target / "input_provenance.json").read_text())
    assert started["status"] == "STARTED_NOT_COMPLETION_PROOF"
    assert completed["status"] == "PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
    assert len(provenance["source_days"]) == 2
    assert completed["verified_available_after"] is False
    assert completed["causal_asof_join_verified"] is False

    snapshots = target / "source_snapshot"
    assert (snapshots / SOURCE.name).is_file()
    assert (snapshots / "r158_ofi_top5_exact_union_v1.py").is_file()
    assert (snapshots / "r158_absorption_components_v1.py").is_file()

    with pytest.raises(FileExistsError, match="IMMUTABLE_SYMBOL_OUTPUT"):
        m.materialize_symbol(
            SYMBOL,
            COMMIT,
            raw_root=raw_root,
            output_root=output_root,
            days=2,
            require_actor=False,
        )


def test_missing_required_source_fails_before_symbol_output_creation(tmp_path):
    raw_root = tmp_path / "raw"
    stage_day(raw_root, START)
    runs_parent = tmp_path / "runs"
    runs_parent.mkdir()
    output_root = runs_parent / "r158"

    with pytest.raises(FileNotFoundError, match="BOOK_SOURCE_MISSING"):
        m.materialize_symbol(
            SYMBOL,
            COMMIT,
            raw_root=raw_root,
            output_root=output_root,
            days=2,
            require_actor=False,
        )
    assert not (output_root / SYMBOL).exists()


@pytest.mark.parametrize("commit", ["short", "g" * 40, "A" * 40])
def test_source_commit_must_be_exact_lowercase_sha(commit, tmp_path):
    runs_parent = tmp_path / "runs"
    runs_parent.mkdir()
    with pytest.raises(ValueError, match="SOURCE_COMMIT"):
        m.materialize_symbol(
            SYMBOL,
            commit,
            raw_root=tmp_path / "raw",
            output_root=runs_parent / "r158",
            days=1,
            require_actor=False,
        )
