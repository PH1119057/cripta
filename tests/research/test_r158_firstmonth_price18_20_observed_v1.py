"""#18/#20 first-month observed-only synthetic first-pass and independent QC."""
import datetime as dt
import gzip
import importlib.util
import json
import os
import pathlib
import types

import pytest

BASE = pathlib.Path(__file__).resolve().parents[2] / (
    "research/microstructure/experiments"
)


def load(file):
    target = BASE / file
    spec = importlib.util.spec_from_file_location(file.replace(".", "_"), target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load("r158_firstmonth_price18_20_observed_v1.py")
Q = load("r158_firstmonth_price18_20_observed_independent_qc_v1.py")


def fixture(tmp_path, monkeypatch, *, corrupt_day=None):
    import pwd

    repo = tmp_path / "repo"
    ref = repo / ".git/refs/heads/main"
    ref.parent.mkdir(parents=True)
    ref.write_text("current-test-commit\n")
    raw = tmp_path / "raw"
    research = tmp_path / "research"
    research.mkdir()
    sources = []
    for day_index in range(30):
        day = M.FIRST + dt.timedelta(days=day_index)
        source = raw / "APTUSDT" / "public_trades" / (
            "APTUSDT" + day.isoformat() + ".csv.gz"
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        if day_index < 2:
            start = int(dt.datetime.combine(
                day, dt.time(), dt.UTC
            ).timestamp())
            with gzip.open(source, "wt") as data:
                data.write("timestamp,price,foreignNotional\n")
                for minute in range(1440):
                    # A real missing print minute, including a UTC day boundary.
                    if minute in (75, 1439) and day_index == 0:
                        continue
                    price = 100 + (minute % 23) * 0.013 + day_index * 0.17
                    stamp = start + 60 * minute + 0.0001
                    if corrupt_day == day_index and minute == 1:
                        stamp = start + 86401
                    data.write(f"{stamp:.4f},{price:.6f},123\n")
            actual_sha = M.digest(source)
            size = source.stat().st_size
        else:
            actual_sha = "0" * 64
            size = 0
        sources.append({
            "symbol": "APTUSDT",
            "date_utc": day.isoformat(),
            "compressed_bytes": size,
            "compressed_sha256": actual_sha,
        })
    manifest = tmp_path / "source_manifest.json"
    manifest.write_text(json.dumps({"source_files": sources}))
    monkeypatch.setattr(M, "RAW", raw)
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "OUT", research / "price18_20")
    monkeypatch.setattr(M, "RAW_MANIFEST", manifest)
    monkeypatch.setattr(Q, "RAW", raw)
    monkeypatch.setattr(Q, "OUT", M.OUT)
    monkeypatch.setattr(
        pwd, "getpwnam", lambda _: types.SimpleNamespace(pw_uid=os.geteuid())
    )
    return M.OUT / "physical_2d" / "APTUSDT"


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix actual actor only")
def test_two_day_18_20_full_raw_replay_with_missing_minutes(tmp_path, monkeypatch):
    dest = fixture(tmp_path, monkeypatch)
    M.run("APTUSDT", 2)
    original = json.loads((dest / "result.json").read_text())
    assert original["rows"] == 2880
    assert original["indicator_ids"] == [18, 20]
    assert original["status"] == M.COMPLETE
    with gzip.open(dest / "features.jsonl.gz", "rt") as data:
        rows = [json.loads(line) for line in data]
    assert len(rows) == 2880
    assert rows[74]["trade_present"] is True
    assert rows[75]["trade_present"] is False
    assert rows[75]["minute_trade_close"] is None
    assert rows[75]["returns_autocorr_lag1_prev60"] is None
    assert rows[75]["quality_20"] == "NO_CURRENT_TRADE_MINUTE_CLOSE"
    assert rows[76]["quality_20"] == (
        "MISSING_PRIOR60_OBSERVED_MINUTE_CLOSES"
    )
    assert rows[136]["quality_20"] == (
        "OBSERVED_PRIOR60_TRADE_CLOSE_WINDOW"
    )
    assert rows[74]["returns_autocorr_lag1_prev60"] is not None
    assert rows[1439]["minute_trade_close"] is None
    assert rows[1440]["quality_20"] == (
        "MISSING_PRIOR60_OBSERVED_MINUTE_CLOSES"
    )
    assert rows[1502]["returns_autocorr_lag1_prev60"] is not None
    assert all(row["observed_at_utc"] is None for row in rows)
    Q.check("APTUSDT", 2)
    receipt = json.loads((dest / "independent_qc_report.json").read_text())
    assert receipt["status"] == Q.PASS
    assert receipt["verified_minute_rows"] == 2880
    assert receipt["source_continuity_verified"] is False
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        M.run("APTUSDT", 2)
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        Q.check("APTUSDT", 2)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix actual actor only")
def test_invalid_second_day_keeps_partial_artifacts(tmp_path, monkeypatch):
    dest = fixture(tmp_path, monkeypatch, corrupt_day=1)
    with pytest.raises(ValueError, match="OUTSIDE_UTC_DAY|NOT_MONOTONE"):
        M.run("APTUSDT", 2)
    status = json.loads((dest / "run_manifest.json").read_text())
    assert status["status"] == "FAILED"
    assert (dest / "source_snapshot/materializer.py").exists()
    assert (dest / "features.jsonl.gz.part").exists()
    assert not (dest / "result.json").exists()


def test_autocorr_and_extrema_refuse_missing_inputs():
    from collections import deque
    from decimal import Decimal

    missing = deque([Decimal("0.01")] * 59 + [None], maxlen=60)
    v, status = M.lag1_of_60_returns(missing)
    assert v is None
    assert status == "MISSING_CONSECUTIVE_OBSERVED_1M_RETURNS"
    constant = deque([Decimal("0")] * 60, maxlen=60)
    v, status = M.lag1_of_60_returns(constant)
    assert v is None
    assert status == "ZERO_LOG_RETURN_VARIANCE"
