"""#14 first-month current flow / next close only LABEL: gap and QC tests."""
import datetime as dt
import gzip
import importlib.util
import json
import os
import pathlib
import types

import pytest

DIR = pathlib.Path(__file__).resolve().parents[2] / "research/microstructure/experiments"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), DIR / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load("r158_firstmonth_flow14_next_minute_LABEL_v1.py")
Q = load("r158_firstmonth_flow14_next_minute_LABEL_independent_qc_v1.py")


def setup(tmp, monkeypatch, corrupt=False):
    import pwd
    raw = tmp / "raw"
    repo = tmp / "repo"
    commit = repo / ".git/refs/heads/main"
    commit.parent.mkdir(parents=True)
    commit.write_text("local-test-head\n")
    out = tmp / "runs" / "flow14"
    out.parent.mkdir()
    entries = []
    for d in range(30):
        date = M.FIRST + dt.timedelta(days=d)
        path = raw / "APTUSDT/public_trades" / ("APTUSDT" + date.isoformat() + ".csv.gz")
        path.parent.mkdir(parents=True, exist_ok=True)
        if d < 2:
            epoch = int(dt.datetime.combine(date, dt.time(), dt.UTC).timestamp())
            with gzip.open(path, "wt") as f:
                f.write("timestamp,price,foreignNotional,side\n")
                for minute in range(1440):
                    if d == 0 and minute in (74, 1439):
                        continue
                    timestamp = epoch + 60 * minute + DecimalToFloat(0.0001)
                    if corrupt and d == 1 and minute == 200:
                        timestamp = epoch + 86401
                    px = 100 + (minute % 17) * 0.005 + d * 0.1
                    f.write(f"{timestamp:.4f},{px:.5f},{10+minute%7},"
                            + ("Buy" if minute%2 else "Sell") + "\n")
            digest = M.sha(path)
            size = path.stat().st_size
        else:
            digest, size = "0" * 64, 0
        entries.append({"symbol": "APTUSDT", "date_utc": date.isoformat(),
                        "compressed_sha256": digest, "compressed_bytes": size})
    source_manifest = tmp / "raw_manifest.json"
    source_manifest.write_text(json.dumps({"source_files": entries}))
    monkeypatch.setattr(M, "RAW", raw)
    monkeypatch.setattr(M, "OUT", out)
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "MANIFEST", source_manifest)
    monkeypatch.setattr(Q, "RAW", raw)
    monkeypatch.setattr(Q, "ROOT", out)
    monkeypatch.setattr(pwd, "getpwnam",
                        lambda _: types.SimpleNamespace(pw_uid=os.geteuid()))
    return out / "physical_2d" / "APTUSDT"


def DecimalToFloat(value):
    return value


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix actor only")
def test_full_two_day_label_at_utc_midnight(tmp_path, monkeypatch):
    dest = setup(tmp_path, monkeypatch)
    M.run("APTUSDT", 2)
    with gzip.open(dest / "features_1m.jsonl.gz", "rt") as f:
        data = [json.loads(line) for line in f]
    assert len(data) == 2880
    assert data[73]["label_quality"] == "NO_FOLLOWING_MINUTE_TRADE"
    assert data[74]["label_quality"] == "NO_CURRENT_TRADE"
    assert data[74]["current_minute_trade_close"] is None
    assert data[1438]["label_quality"] == "NO_FOLLOWING_MINUTE_TRADE"
    assert data[1439]["label_quality"] == "NO_CURRENT_TRADE"
    assert data[1440]["label_quality"] == "PAIRED_NEXT_MINUTE_FUTURE_LABEL_ONLY"
    assert data[2879]["label_quality"] == "NO_FOLLOWING_MINUTE_INSIDE_SCOPE"
    assert data[1440]["future_label_verified_available_after_utc"] is None
    assert data[1440]["derived_feature_window_end_utc"] == "2026-05-19T00:01:00+00:00"
    assert data[1440]["future_label_window_end_utc"] == "2026-05-19T00:02:00+00:00"
    assert all(row["verified_available_after_utc"] is None for row in data)
    report = json.loads((dest / "result.json").read_text())
    assert report["future_label_not_feature"] is True
    assert report["marginal_order_price_impact_verified"] is False
    Q.run("APTUSDT", 2)
    qc = json.loads((dest / "independent_qc_report.json").read_text())
    assert qc["status"] == Q.PASS
    assert qc["verified_minute_rows"] == 2880
    assert len(qc["source_days"]) == 2
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        M.run("APTUSDT", 2)
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        Q.run("APTUSDT", 2)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix actor only")
def test_bad_raw_stays_failed_and_never_overwrites(tmp_path, monkeypatch):
    folder = setup(tmp_path, monkeypatch, corrupt=True)
    with pytest.raises(ValueError, match="TRADE_OUTSIDE_DATE|OUT_OF_ORDER_SOURCE"):
        M.run("APTUSDT", 2)
    assert (folder / "source_snapshot/materializer.py").exists()
    assert json.loads((folder / "run_manifest.json").read_text())["status"] == "FAILED"
    assert not (folder / "result.json").exists()


def test_no_future_available_as_current():
    from decimal import Decimal
    cur = {"count": 1, "signed": Decimal("20"), "close": Decimal("100")}
    nxt = {"count": 1, "signed": Decimal("4"), "close": Decimal("102")}
    assert M.paired_label(cur, nxt) == (
        Decimal("200"), "PAIRED_NEXT_MINUTE_FUTURE_LABEL_ONLY"
    )
    assert M.paired_label(cur, None)[1] == "NO_FOLLOWING_MINUTE_INSIDE_SCOPE"
