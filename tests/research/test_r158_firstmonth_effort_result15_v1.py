"""Synthetic first-month #15 physical/independent-QC, nulls and immutable outputs."""
import datetime as dt
import gzip
import importlib.util
import json
import os
import pathlib
import pwd
import types
from decimal import Decimal

import pytest

BASE = pathlib.Path(__file__).resolve().parents[2] / (
    "research/microstructure/experiments"
)


def load(name):
    file = BASE / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load("r158_firstmonth_effort_result15_v1.py")
Q = load("r158_firstmonth_effort_result15_independent_qc_v1.py")


def archive(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as output:
        output.write("timestamp,price,foreignNotional\n")
        for t, p, n in records:
            output.write(f"{t},{p},{n}\n")


def create_fixture(tmp_path, monkeypatch, *, error_day=None):
    root = tmp_path / "raw"
    output_root = tmp_path / "research"
    output_root.mkdir()
    repo = tmp_path / "repo"
    sha = repo / ".git/refs/heads/main"
    sha.parent.mkdir(parents=True)
    sha.write_text("test-source-commit\n")
    entries = []
    for i in range(30):
        day = M.FIRST + dt.timedelta(days=i)
        path = root / "APTUSDT" / "public_trades" / (
            "APTUSDT" + day.isoformat() + ".csv.gz"
        )
        stamp = Decimal(int(dt.datetime.combine(
            day, dt.time(), dt.UTC
        ).timestamp()))
        if i < 2:
            records = [
                (str(stamp + Decimal("10.0001")), "100", "1000"),
                (str(stamp + Decimal("12.0005")), "100.01", "2000"),
                (str(stamp + Decimal("180.001")), "101", "3000"),
            ]
            if i == error_day:
                records[0] = (str(stamp + Decimal(86401)), "100", "1000")
            archive(path, records)
            digest = M.sha(path)
            size = path.stat().st_size
        else:
            digest = "0" * 64
            size = 0
        entries.append({
            "symbol": "APTUSDT", "date_utc": day.isoformat(),
            "compressed_sha256": digest, "compressed_bytes": size,
        })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_files": entries}))
    monkeypatch.setattr(M, "ROOT", root)
    monkeypatch.setattr(M, "OUT", output_root / "r158")
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "MANIFEST", manifest)
    monkeypatch.setattr(Q, "RAW", root)
    monkeypatch.setattr(Q, "OUT", M.OUT)
    monkeypatch.setattr(
        pwd, "getpwnam", lambda user: types.SimpleNamespace(pw_uid=os.geteuid())
    )
    return M.OUT / "physical_2d/APTUSDT"


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix actor check")
def test_two_day_primary_independent_full_rows_and_no_data(tmp_path, monkeypatch):
    dest = create_fixture(tmp_path, monkeypatch)
    M.run("APTUSDT", 2)
    result = json.loads((dest / "result.json").read_text())
    assert result["status"] == M.PHYSICAL
    assert result["rows"] == 2880
    assert result["fills"] == 6
    manifest = json.loads((dest / "run_manifest.json").read_text())
    assert manifest["status"] == M.PHYSICAL
    with gzip.open(dest / "features.jsonl.gz", "rt") as f:
        first = json.loads(next(f))
        empty = json.loads(next(f))
    assert first["fill_count"] == 2
    assert first["first_trade_price"] == "100"
    assert first["last_trade_price"] == "100.01"
    assert first["quote_turnover_usdt"] == "3000"
    assert Decimal(first["effort_result_bps_per_million_usdt"]) > 0
    assert empty["fill_count"] == 0
    assert empty["quality"] == "NO_PRINTS_IN_MINUTE"
    assert empty["effort_result_bps_per_million_usdt"] is None
    Q.check("APTUSDT", 2)
    receipt = json.loads((dest / "independent_qc_report.json").read_text())
    assert receipt["verified_minute_rows"] == 2880
    assert receipt["status"] == Q.PASS
    assert receipt["research_ready"] is False
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        M.run("APTUSDT", 2)
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        Q.check("APTUSDT", 2)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix actor check")
def test_broken_source_fails_and_leaves_partial_for_forensics(tmp_path, monkeypatch):
    dest = create_fixture(tmp_path, monkeypatch, error_day=1)
    with pytest.raises(ValueError, match="NONMONOTONE|OUT_OF_UTC_DAY"):
        M.run("APTUSDT", 2)
    data = json.loads((dest / "run_manifest.json").read_text())
    assert data["status"] == "FAILED"
    assert not (dest / "result.json").exists()
    assert (dest / "source_snapshot/materializer.py").is_file()
    assert (dest / "features.jsonl.gz.part").is_file()


def test_independent_checker_rejects_wrong_numeric_value():
    d = dt.date(2026, 5, 18)
    expected = {
        "quality": "NUMERIC_PHYSICAL_SOURCE_CONTINUITY_UNKNOWN",
        "fill_count": 2,
        "first_trade_price": Decimal("100"),
        "last_trade_price": Decimal("100.01"),
        "quote_turnover_usdt": Decimal("3000"),
        "abs_intraminute_endpoint_bps": Decimal("1"),
        "effort_result_bps_per_million_usdt": Decimal("333.3333"),
    }
    now = dt.datetime.combine(d, dt.time(), dt.UTC)
    row = {
        "instrument_id": "APTUSDT", "indicator_id": 15,
        "venue_id": "Bybit", "formula_version": Q.FORMULA,
        "window_start_utc": now.isoformat(),
        "derived_window_end_utc": (
            now + dt.timedelta(minutes=1)
        ).isoformat(),
        "source_archive_sha256": "ref",
        "observed_at_utc": None,
        "verified_available_after_utc": None,
        "quality": expected["quality"], "fill_count": 2,
        "first_trade_price": "100",
        "last_trade_price": "100.01",
        "quote_turnover_usdt": "3000",
        "abs_intraminute_endpoint_bps": "1",
        "effort_result_bps_per_million_usdt": "1.2345",
    }
    with pytest.raises(ValueError, match="QC_DECIMAL_VALUE_DIFF"):
        Q.compare_row(row, expected, "APTUSDT", d, 0, "ref")
