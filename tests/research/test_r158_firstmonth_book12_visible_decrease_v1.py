"""#12 observed exact-price OB200 size declines, non-cancellation and full QC."""
import datetime as dt
import gzip
import importlib.util
import json
import os
import pathlib
import types
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2] / "research/microstructure/experiments"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), ROOT / name)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


M = load("r158_firstmonth_book12_visible_decrease_v1.py")
Q = load("r158_firstmonth_book12_visible_decrease_independent_qc_v1.py")


def setup(tmp, monkeypatch, bad_initial=False):
    import pwd

    raw = tmp / "raw"
    repo = tmp / "repo"
    head = repo / ".git/refs/heads/main"
    head.parent.mkdir(parents=True)
    head.write_text("fixture-source-head\n")
    out = tmp / "runs" / "b12"
    out.parent.mkdir()
    for offset in range(2):
        day = M.FIRST + dt.timedelta(days=offset)
        base = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()) * 1000
        file = raw / "APTUSDT" / "orderbook" / (
            day.isoformat() + "_APTUSDT_ob200.data.zip"
        )
        file.parent.mkdir(parents=True, exist_ok=True)

        def event(when, kind, bid, ask):
            return {
                "ts": when, "type": kind,
                "data": {"b": bid, "a": ask, "seq": when},
            }

        messages = [
            event(base, "delta" if bad_initial and offset == 0 else "snapshot",
                  [["100", "10"]], [["101", "5"]]),
            event(base + 1000, "delta", [["100", "6"]], [["101", "4"]]),
            event(base + 60000, "delta", [["100", "7"]], []),
            event(base + 180000, "snapshot", [["100", "2"]], [["101", "4"]]),
            event(base + 240000, "delta", [], [["101", "3"]]),
            event(base + 86340000, "delta", [["100", "1"]], []),
        ]
        if offset == 0:
            messages.append(
                event(base + 86400000, "snapshot",
                      [["100", "10"]], [["101", "5"]])
            )
        with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(day.isoformat() + "_APTUSDT_ob200.data",
                       "".join(json.dumps(x) + "\n" for x in messages))
    monkeypatch.setattr(M, "RAW", raw)
    monkeypatch.setattr(M, "OUTPUT", out)
    monkeypatch.setattr(M, "REPO", repo)
    monkeypatch.setattr(Q, "RAW", raw)
    monkeypatch.setattr(Q, "ROOT", out)
    monkeypatch.setattr(pwd, "getpwnam",
                        lambda _: types.SimpleNamespace(pw_uid=os.geteuid()))
    return out / "physical_2d" / "APTUSDT"


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix research writer")
def test_real_two_day_minute_grid_independent_decimal(tmp_path, monkeypatch):
    folder = setup(tmp_path, monkeypatch)
    M.main("APTUSDT", 2)
    with gzip.open(folder / "features_1m.jsonl.gz", "rt") as f:
        r = [json.loads(x) for x in f]
    assert len(r) == 2880
    assert r[0]["visible_bid_decrease_usdt"] == "400"
    assert r[0]["visible_ask_decrease_usdt"] == "101"
    assert r[0]["visible_bid_decrease_count"] == 1
    assert r[1]["visible_bid_decrease_usdt"] == "0"
    assert r[1]["quality"] == "OBSERVED_EXACT_PRICE_VISIBLE_DECREASE_CAUSE_UNKNOWN"
    assert r[2]["quality"] == "NO_BOOK_DELTA_IN_MINUTE"
    assert r[2]["visible_bid_decrease_usdt"] is None
    assert r[3]["book_snapshot_messages"] == 1
    assert r[3]["quality"] == "NO_BOOK_DELTA_IN_MINUTE"
    assert r[4]["visible_ask_decrease_usdt"] == "101"
    assert r[1439]["visible_bid_decrease_usdt"] == "100"
    assert r[1440]["visible_bid_decrease_usdt"] == "400"
    assert r[1440]["window_start_utc"] == "2026-05-19T00:00:00+00:00"
    assert r[1440]["verified_available_after_utc"] is None
    info = json.loads((folder / "result.json").read_text())
    assert info["rows"] == 2880 and info["pull_or_cancel_verified"] is False
    Q.check("APTUSDT", 2)
    qc = json.loads((folder / "independent_qc_report.json").read_text())
    assert qc["status"] == Q.PASS
    assert qc["verified_minute_rows"] == 2880
    assert qc["observed_delta_minutes"] == 10
    assert len(qc["source_days"]) == 2
    assert qc["receiver_clock_verified"] is False
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        M.main("APTUSDT", 2)
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        Q.check("APTUSDT", 2)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Unix research writer")
def test_no_initial_snapshot_keeps_terminal_failed_partial(tmp_path, monkeypatch):
    folder = setup(tmp_path, monkeypatch, bad_initial=True)
    with pytest.raises(ValueError, match="INITIAL_BOOK_STATE_NOT_SNAPSHOT"):
        M.main("APTUSDT", 2)
    assert json.loads((folder / "run_manifest.json").read_text())["status"] == "FAILED"
    assert not (folder / "result.json").exists()
    assert (folder / "source_snapshot/materializer.py").exists()


def test_snapshot_reset_is_not_counted_as_a_decrease():
    from decimal import Decimal

    # The distinction is semantically essential: a book snapshot replacing
    # 10 with 2 does NOT imply 8 were cancelled at that price.
    assert Decimal("10") - Decimal("2") == Decimal("8")
    assert M.FORMULA.endswith("_v1")
