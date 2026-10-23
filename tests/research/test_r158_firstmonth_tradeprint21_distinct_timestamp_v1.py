"""#21 distinct timestamp trade-print proxy: tie ambiguity, UTC continuity, QC."""
import datetime as dt
import gzip
import importlib.util
import json
import os
import pathlib
import types

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "research/microstructure/experiments"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), SCRIPTS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load("r158_firstmonth_tradeprint21_distinct_timestamp_v1.py")
Q = load("r158_firstmonth_tradeprint21_distinct_timestamp_independent_qc_v1.py")


def setup_source(tmp, monkeypatch, corrupt=False):
    import pwd

    raw = tmp / "raw"
    repo = tmp / "repo"
    git_head = repo / ".git/refs/heads/main"
    git_head.parent.mkdir(parents=True)
    git_head.write_text("synthetic-exact-source-head\n")
    root = tmp / "runs" / "print21"
    root.parent.mkdir()
    sources = []
    for index in range(30):
        day = M.FIRST + dt.timedelta(days=index)
        archive = raw / "APTUSDT" / "public_trades" / (
            "APTUSDT" + day.isoformat() + ".csv.gz")
        archive.parent.mkdir(parents=True, exist_ok=True)
        if index < 2:
            unix = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp())
            with gzip.open(archive, "wt", newline="") as f:
                f.write("timestamp,price,side\n")
                for minute in range(1440):
                    if index == 0 and minute in (75, 1439):
                        continue
                    epoch = unix + minute * 60
                    if corrupt and index == 1 and minute == 15:
                        epoch += 86400
                    if index == 0 and minute == 60:
                        # Both sides of prior high=low=100 at SAME timestamp.
                        # No strict temporal ordering can be inferred.
                        f.write(f"{epoch+0.1:.4f},102,Buy\n")
                        f.write(f"{epoch+0.1:.4f},99,Sell\n")
                    elif index == 0 and minute == 61:
                        f.write(f"{epoch+0.1:.4f},98,Sell\n")
                        f.write(f"{epoch+0.2:.4f},100,Buy\n")
                    elif index == 0 and minute == 62:
                        f.write(f"{epoch+0.1:.4f},103,Buy\n")
                        f.write(f"{epoch+0.2:.4f},100,Sell\n")
                    else:
                        f.write(f"{epoch+0.1:.4f},100,Buy\n")
            digest, length = M.sha(archive), archive.stat().st_size
        else:
            digest, length = "0" * 64, 0
        sources.append({"symbol": "APTUSDT", "date_utc": day.isoformat(),
                        "compressed_sha256": digest, "compressed_bytes": length})
    manifest = tmp / "raw_manifest.json"
    manifest.write_text(json.dumps({"source_files": sources}))
    monkeypatch.setattr(M, "RAW", raw)
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "MANIFEST", manifest)
    monkeypatch.setattr(M, "OUT", root)
    monkeypatch.setattr(Q, "RAW", raw)
    monkeypatch.setattr(Q, "ROOT", root)
    monkeypatch.setattr(
        pwd, "getpwnam", lambda _: types.SimpleNamespace(pw_uid=os.geteuid()))
    return root / "physical_2d" / "APTUSDT"


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix effective actor required")
def test_month_slice_two_days_full_raw_distinct_group_replay(tmp_path, monkeypatch):
    dest = setup_source(tmp_path, monkeypatch)
    M.run("APTUSDT", 2)
    with gzip.open(dest / "features_1m.jsonl.gz", "rt") as f:
        records = [json.loads(line) for line in f]
    assert len(records) == 2880
    assert records[0]["quality"] == "INSUFFICIENT_PRIOR60_MINUTES"
    assert records[59]["high_trade_print_cross"] is None
    assert records[60]["quality"] == "OBSERVED_PRIOR60_AND_CURRENT_PRINTS"
    assert records[60]["high_trade_print_cross"] is True
    assert records[60]["low_trade_print_cross"] is True
    assert records[60]["high_reentry_later_timestamp"] is False
    assert records[60]["low_reentry_later_timestamp"] is False
    assert records[60]["high_same_timestamp_ambiguous"] is True
    assert records[60]["low_same_timestamp_ambiguous"] is True
    assert records[60]["multiprice_tied_groups"] == 1
    assert records[61]["low_trade_print_cross"] is True
    assert records[61]["low_reentry_later_timestamp"] is True
    assert records[62]["high_trade_print_cross"] is True
    assert records[62]["high_reentry_later_timestamp"] is True
    assert records[75]["quality"] == "NO_CURRENT_PRINT"
    assert records[76]["quality"] == "PRIOR60_CONTAINS_NO_PRINT_MINUTE"
    assert records[135]["quality"] == "PRIOR60_CONTAINS_NO_PRINT_MINUTE"
    assert records[136]["quality"] == "OBSERVED_PRIOR60_AND_CURRENT_PRINTS"
    assert records[1439]["quality"] == "NO_CURRENT_PRINT"
    assert records[1440]["quality"] == "PRIOR60_CONTAINS_NO_PRINT_MINUTE"
    assert records[1500]["quality"] == "OBSERVED_PRIOR60_AND_CURRENT_PRINTS"
    assert records[1440]["window_start_utc"] == "2026-05-19T00:00:00+00:00"
    assert records[1440]["verified_available_after_utc"] is None
    Q.verify("APTUSDT", 2)
    qc = json.loads((dest / "independent_qc_report.json").read_text())
    assert qc["status"] == Q.PASS
    assert qc["verified_minute_rows"] == 2880
    assert len(qc["source_days"]) == 2
    assert qc["real_orderbook_sweep_verified"] is False
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        M.run("APTUSDT", 2)
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        Q.verify("APTUSDT", 2)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix effective actor required")
def test_bad_source_preserves_failed_partial_artifacts(tmp_path, monkeypatch):
    dest = setup_source(tmp_path, monkeypatch, corrupt=True)
    with pytest.raises(ValueError, match="TRADE_OUTSIDE_SOURCE_UTC_DAY"):
        M.run("APTUSDT", 2)
    manifest = json.loads((dest / "run_manifest.json").read_text())
    assert manifest["status"] == "FAILED"
    assert (dest / "source_snapshot/materializer.py").exists()
    assert not (dest / "result.json").exists()


def test_timestamp_tie_is_never_assumed_ordered():
    from collections import deque
    from decimal import Decimal

    history = deque([(1, Decimal(100), Decimal(100))] * 60, maxlen=60)
    tie = {"fills": 2, "groups": [[Decimal("1.1"), Decimal(99), Decimal(102), 2]]}
    value, status = M.evaluate(history, tie)
    assert status == "OBSERVED_PRIOR60_AND_CURRENT_PRINTS"
    assert value["high_crossed"] and value["low_crossed"]
    assert not value["high_reentry_later_timestamp"]
    assert not value["low_reentry_later_timestamp"]
    assert value["high_same_timestamp_ambiguous"]
    later = {"fills": 3, "groups": [
        [Decimal("1.1"), Decimal(99), Decimal(102), 2],
        [Decimal("1.2"), Decimal(100), Decimal(100), 1]]}
    value, _ = M.evaluate(history, later)
    assert value["high_reentry_later_timestamp"]
    assert value["low_reentry_later_timestamp"]
