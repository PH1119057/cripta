"""Synthetic first-month #17 proxy, independent full-block source re-check."""
import datetime as dt
import gzip
import importlib.util
import json
import os
import pathlib
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2] / (
    "research/microstructure/experiments"
)


def load(filename):
    spec = importlib.util.spec_from_file_location(
        filename.replace(".", "_"), ROOT / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load("r158_firstmonth_flow_decay17_proxy_v1.py")
Q = load("r158_firstmonth_flow_decay17_proxy_independent_qc_v1.py")


def make_fixture(tmp_path, monkeypatch, bad_timestamp=False):
    import pwd

    raw = tmp_path / "raw"
    source_root = tmp_path / "repo"
    commitfile = source_root / ".git/refs/heads/main"
    commitfile.parent.mkdir(parents=True)
    commitfile.write_text("synthetic-source-commit\n")
    out = tmp_path / "results" / "flow17"
    out.parent.mkdir()
    all_sources = []
    for day_index in range(30):
        day = M.FIRST + dt.timedelta(days=day_index)
        path = raw / "APTUSDT" / "public_trades" / (
            "APTUSDT" + day.isoformat() + ".csv.gz"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if day_index < 2:
            epoch = int(dt.datetime.combine(
                day, dt.time(), dt.UTC
            ).timestamp())
            with gzip.open(path, "wt") as stream:
                stream.write("timestamp,price,foreignNotional,side\n")
                for block in range(288):
                    # One missing 5m block and one at UTC boundary.
                    if day_index == 0 and block in (70, 287):
                        continue
                    price = 100 + (day_index * 288 + block) * 0.01
                    notional = 15 + (block % 7) * 9
                    ts = epoch + block * 300 + 0.0001
                    if bad_timestamp and day_index == 1 and block == 20:
                        ts = epoch + 86410
                    stream.write(f"{ts:.4f},{price:.5f},{notional},Buy\n")
            sha = M.sha(path)
            size = path.stat().st_size
        else:
            sha, size = "0" * 64, 0
        all_sources.append({
            "symbol": "APTUSDT", "date_utc": day.isoformat(),
            "compressed_sha256": sha, "compressed_bytes": size,
        })
    manifest = tmp_path / "source_manifest.json"
    manifest.write_text(json.dumps({"source_files": all_sources}))
    monkeypatch.setattr(M, "RAW", raw)
    monkeypatch.setattr(M, "REPO", source_root)
    monkeypatch.setattr(M, "OUT", out)
    monkeypatch.setattr(M, "MANIFEST", manifest)
    monkeypatch.setattr(Q, "RAW", raw)
    monkeypatch.setattr(Q, "OUT", out)
    monkeypatch.setattr(
        pwd, "getpwnam",
        lambda _: types.SimpleNamespace(pw_uid=os.geteuid())
    )
    return out / "physical_2d" / "APTUSDT"


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix writer only")
def test_real_2day_shape_missing_and_independent_qc(tmp_path, monkeypatch):
    dest = make_fixture(tmp_path, monkeypatch)
    M.run("APTUSDT", 2)
    result = json.loads((dest / "result.json").read_text())
    assert result["rows"] == 576
    assert result["future_return_used"] is False
    assert result["decays"] > 0
    assert result["eligible"] > 0
    with gzip.open(dest / "features_5m.jsonl.gz", "rt") as file:
        rows = [json.loads(s) for s in file]
    assert len(rows) == 576
    assert rows[0]["quality"] == "INSUFFICIENT_PRECEDING_BLOCKS"
    assert rows[2]["quality"].startswith("ELIGIBLE_")
    assert rows[70]["quality"] == "MISSING_OBSERVED_5M_BLOCK"
    assert rows[287]["quality"] == "MISSING_OBSERVED_5M_BLOCK"
    assert rows[288]["quality"] == "MISSING_OBSERVED_5M_BLOCK"
    assert rows[289]["quality"] == "MISSING_OBSERVED_5M_BLOCK"
    assert rows[290]["quality"].startswith("ELIGIBLE_")
    assert rows[291]["quality"].startswith("ELIGIBLE_")
    assert rows[288]["window_start_utc"] == "2026-05-19T00:00:00+00:00"
    assert rows[291]["observed_at_utc"] is None
    assert rows[291]["verified_available_after_utc"] is None
    Q.verify("APTUSDT", 2)
    receipt = json.loads((dest / "independent_qc_report.json").read_text())
    assert receipt["status"] == Q.QC_PASS
    assert receipt["verified_5m_rows"] == 576
    assert receipt["research_ready"] is False
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        M.run("APTUSDT", 2)
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        Q.verify("APTUSDT", 2)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix writer only")
def test_bad_trade_timestamp_keeps_partial_for_forensics(tmp_path, monkeypatch):
    dest = make_fixture(tmp_path, monkeypatch, bad_timestamp=True)
    with pytest.raises(ValueError, match="OUT_OF_UTC_DATE_TRADE|NONMONOTONE"):
        M.run("APTUSDT", 2)
    receipt = json.loads((dest / "run_manifest.json").read_text())
    assert receipt["status"] == "FAILED"
    assert not (dest / "result.json").exists()
    assert (dest / "source_snapshot/materializer.py").exists()


def test_zero_flow_or_absent_block_never_faked():
    from decimal import Decimal

    b0 = {"last": Decimal(100), "count": 1, "flow": Decimal(30)}
    b1 = {"last": Decimal(101), "count": 1, "flow": Decimal(40)}
    b2 = {"last": Decimal(102), "count": 1, "flow": Decimal(20)}
    ratio, decay, q = M.as_of_feature(b0, b1, b2)
    assert ratio == Decimal("0.5")
    assert decay and q == "ELIGIBLE_FLOW_DECAY"
    b2["count"] = 0
    assert M.as_of_feature(b0, b1, b2)[2] == "MISSING_OBSERVED_5M_BLOCK"
    b2["count"] = 1
    b2["flow"] = Decimal(0)
    assert M.as_of_feature(b0, b1, b2)[2] == "ZERO_SIGNED_FLOW"
