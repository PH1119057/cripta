"""#30 Top5 stock recovery: right censor, snapshot, same timestamp and raw QC."""
import datetime as dt
import gzip
import importlib.util
import json
import os
import pathlib
import types
import zipfile

import pytest

PATH = pathlib.Path(__file__).resolve().parents[2] / "research/microstructure/experiments"


def module(filename):
    spec = importlib.util.spec_from_file_location(filename.replace(".", "_"), PATH / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


M = module("r158_firstmonth_book30_top5_stock_recovery_v1.py")
Q = module("r158_firstmonth_book30_top5_stock_recovery_independent_qc_v1.py")


def source_fixture(tmp, monkeypatch, no_snapshot=False):
    import pwd

    raw = tmp / "raw"
    repo = tmp / "source"
    commit = repo / ".git/refs/heads/main"
    commit.parent.mkdir(parents=True)
    commit.write_text("fixture-head\n")
    output = tmp / "research/runs/book30"
    output.parent.mkdir(parents=True)
    for index in range(2):
        day = M.FIRST + dt.timedelta(days=index)
        source = raw / "APTUSDT/orderbook" / (
            day.isoformat() + "_APTUSDT_ob200.data.zip")
        source.parent.mkdir(parents=True, exist_ok=True)
        beginning = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()) * 1000

        def e(ms, kind, b=(), a=()):
            return {"ts": beginning + ms, "type": kind,
                    "data": {"b": list(b), "a": list(a)}}

        events = [
            e(0, "delta" if no_snapshot and index == 0 else "snapshot",
              [["100", "10"]], [["101", "10"]]),
            e(1000, "delta", [["100", "7"]]),
            e(2000, "delta", [["100", "10"]]),
            e(3000, "delta", a=[["101", "7"]]),
            e(4000, "delta", a=[["101", "10"]]),
            e(60000, "delta", [["100", "8"]]),
            e(121001, "delta", [["100", "8"]]),  # first event after deadline: expire
            e(121100, "delta", [["100", "6"]]),  # new shock
            e(121100, "delta", [["100", "8"]]),  # same timestamp: ambiguous
            e(180000, "snapshot", [["100", "10"]], [["101", "10"]]),
            e(240000, "delta", a=[["101", "8"]]),  # pending ask
            e(300000, "snapshot", [["100", "10"]], [["101", "10"]]),  # censor ask
            e(86340000, "delta", [["100", "9"]]),  # right-censor at day end
        ]
        if index == 0:
            events.append(e(86400000, "snapshot", [["100", "10"]], [["101", "10"]]))
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(day.isoformat()+"_APTUSDT_ob200.data",
                       "".join(json.dumps(x)+"\n" for x in events))
    monkeypatch.setattr(M, "ROOT", raw)
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(M, "DEST", output)
    monkeypatch.setattr(Q, "RAW", raw)
    monkeypatch.setattr(Q, "BASE", output)
    monkeypatch.setattr(pwd, "getpwnam",
                        lambda _: types.SimpleNamespace(pw_uid=os.geteuid()))
    return output / "physical_2d/APTUSDT"


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix effective actor")
def test_real_2d_episode_accounting_independent_replay(tmp_path, monkeypatch):
    folder = source_fixture(tmp_path, monkeypatch)
    M.main("APTUSDT", 2)
    with gzip.open(folder/"features_1m.jsonl.gz", "rt") as f:
        rows = [json.loads(r) for r in f]
    assert len(rows) == 2880
    assert rows[0]["bid_opened"] == 1
    assert rows[0]["bid_recovered"] == 1
    assert rows[0]["bid_recovery_ms_total"] == 1000
    assert rows[0]["ask_opened"] == 1
    assert rows[0]["ask_recovered"] == 1
    assert rows[1]["bid_opened"] == 1
    assert rows[1]["bid_recovered"] == 0
    assert rows[2]["bid_observed_expired"] == 1
    assert rows[2]["bid_opened"] == 1
    assert rows[2]["bid_same_timestamp_ambiguous"] == 1
    assert rows[2]["bid_recovered"] == 0
    assert rows[3]["quality"] == "NO_BOOK_DELTA_IN_MINUTE"
    assert rows[3]["bid_recovered"] is None
    assert rows[3]["bid_snapshot_censored"] == 0
    assert rows[4]["ask_opened"] == 1
    assert rows[5]["ask_snapshot_censored"] == 1
    assert rows[5]["bid_opened"] is None
    assert rows[1439]["bid_opened"] == 1
    assert rows[1440]["window_start_utc"] == "2026-05-19T00:00:00+00:00"
    assert rows[1440]["observed_at_utc"] is None
    original = json.loads((folder/"result.json").read_text())
    assert original["rows"] == 2880 and original["days"] == 2
    assert original["bid"]["opened"] == 8
    assert original["bid"]["recovered"] == 2
    assert original["bid"]["observed_expired"] == 2
    assert original["bid"]["same_timestamp_ambiguous"] == 2
    assert original["bid"]["day_end_censored"] == 2
    assert original["ask"]["opened"] == 4
    assert original["ask"]["recovered"] == 2
    assert original["ask"]["snapshot_censored"] == 2
    assert original["research_ready"] is False
    Q.main("APTUSDT", 2)
    report = json.loads((folder/"independent_qc_report.json").read_text())
    assert report["status"] == Q.PASS
    assert report["verified_minute_rows"] == 2880
    assert len(report["source_days"]) == 2
    assert report["recovered_same_level_or_order_verified"] is False
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        M.main("APTUSDT", 2)
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        Q.main("APTUSDT", 2)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix effective actor")
def test_invalid_initial_book_preserves_failure_manifest(tmp_path, monkeypatch):
    folder = source_fixture(tmp_path, monkeypatch, no_snapshot=True)
    with pytest.raises(ValueError, match="BOOK_INITIAL_SNAPSHOT_REQUIRED"):
        M.main("APTUSDT", 2)
    assert (folder/"source_snapshot/materializer.py").exists()
    assert json.loads((folder/"run_manifest.json").read_text())["status"] == "FAILED"
    assert not (folder/"result.json").exists()


def test_topfive_stock_reprices_without_order_identity():
    from decimal import Decimal

    book = {Decimal(str(p)): Decimal(1) for p in range(100, 107)}
    assert M.top5(book, True) == Decimal(520)
    assert Q.stock(book, "b") == Decimal(520)
    assert M.top5(book, False) == Decimal(510)
    assert Q.stock(book, "a") == Decimal(510)
